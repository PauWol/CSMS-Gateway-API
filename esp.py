import asyncio
import json
import struct
import time

from serial import Serial, SerialException
from models import (
    Command,
    PingResponse,
    SensorResponse,
    LogInfoResponse,
    UartPingResponse,
    StatusResponse,
)

# Command frame format:
COMMAND_PREFIX = "cmd:"
COMMAND_DELIMITER = ";"
COMMAND_TERMINATOR = ":end"

# Command names:
CMD_UART_ACK = "UAK"  # UART physical-layer ack check
CMD_PING = "PNG"  # Liveness check
CMD_STATUS = "STS"  # General status report
CMD_SENSORS = "SNS"  # Read all sensor values
CMD_LOG_INFO = "LGI"  # Log metadata (size, last entry, …)
CMD_LOG_DOWNLOAD = "LGD"  # Stream full log over UART

DEFAULT_TIMEOUT = 5.0

# Protocol flags (must match sender)
FLAG_PIR = 0x01
FLAG_PHC = 0x02
FLAG_RADAR = 0x04
FLAG_THREAT = 0x08
FLAG_SLEEP = 0x10
FLAG_VOLT = 0x20
FLAG_TTE = 0x40

PKT_VERSION = 1
_PHASE_DEC = {0: "DAY", 1: "DUSK", 2: "NIGHT"}

# Minimum header size: version(1) + flags(1) + base_ts(4) = 6 bytes
_MIN_HEADER = 6

# Bytes consumed after the header for each flag
_FLAG_SIZES = {
    FLAG_PIR: 3,  # B + H
    FLAG_PHC: 4,  # H + H
    FLAG_RADAR: 6,  # H + H + H
    FLAG_THREAT: 9,  # f + f + B
    FLAG_SLEEP: 4,  # L
    FLAG_VOLT: 4,  # H + H
    FLAG_TTE: 4,  # L
}


def _required_size(flags: int) -> int:
    """Return the minimum buffer length needed to safely unpack *flags*."""
    return _MIN_HEADER + sum(size for flag, size in _FLAG_SIZES.items() if flags & flag)


def unpack(buf: bytes | bytearray) -> dict:
    """Gateway-side unpack. Returns a dict of present fields."""
    if len(buf) < _MIN_HEADER:
        raise ValueError(
            f"Buffer too short for header: need {_MIN_HEADER}, got {len(buf)}"
        )

    version, flags, base_ts = struct.unpack_from(">BBL", buf, 0)
    offset = 6

    required = _required_size(flags)
    if len(buf) < required:
        raise ValueError(
            f"Buffer too short for declared flags 0x{flags:02x}: "
            f"need {required}, got {len(buf)}"
        )

    out = {"version": version, "flags": flags, "base_ts": base_ts}

    if flags & FLAG_PIR:
        val, delta = struct.unpack_from(">BH", buf, offset)
        out["pir"] = {"value": bool(val), "ts": base_ts + delta}
        offset += 3

    if flags & FLAG_PHC:
        raw, delta = struct.unpack_from(">HH", buf, offset)
        out["phc"] = {"value": raw / 65535, "ts": base_ts + delta}
        offset += 4

    if flags & FLAG_RADAR:
        dist, energy, delta = struct.unpack_from(">HHH", buf, offset)
        out["radar"] = {"distance": dist, "energy": energy, "ts": base_ts + delta}
        offset += 6

    if flags & FLAG_THREAT:
        score, threshold, phase = struct.unpack_from(">ffB", buf, offset)
        out["threat"] = {
            "score": score,
            "threshold": threshold,
            "phase": _PHASE_DEC.get(phase, "UNKNOWN"),
        }
        offset += 9

    if flags & FLAG_SLEEP:
        (ms,) = struct.unpack_from(">L", buf, offset)
        out["sleep_ms"] = ms
        offset += 4

    if flags & FLAG_VOLT:
        raw, delta = struct.unpack_from(">HH", buf, offset)
        out["volt"] = {"value": raw / 1000, "ts": base_ts + delta}
        offset += 4

    if flags & FLAG_TTE:
        (s,) = struct.unpack_from(">L", buf, offset)
        out["tte_s"] = s

    return out


class ESPUart:
    """
    Async-capable UART wrapper for ESP32 peer-to-peer communication.

    background_reader() is the *sole* consumer of the serial port. Every
    incoming byte flows through it:

      • Binary sensor frames   → handle_data()   (updates in-memory state)
      • Command/response frames → _cmd_queue     (consumed by _request / log_download)

    This eliminates the race where background_reader would previously swallow
    a command response that _request was waiting for (the old `pass` branch).
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: int = 1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: Serial | None = None

        self._pir = None
        self._phc = None
        self._radar = None
        self._threat = None
        self._sleep_ms = None
        self._volt = None
        self._tte_s = None
        self.last_sync: int = 0

        # Command responses are delivered here by background_reader.
        # _request and log_download read from this queue.
        self._cmd_queue: asyncio.Queue | None = None

    def init(self):
        """Open the serial port and create the response queue. Call once at startup."""
        try:
            self.serial = Serial(self.port, self.baudrate, timeout=self.timeout)
            self._cmd_queue = asyncio.Queue()
        except Exception as e:
            print(f"[ESPUart] Error initializing serial connection: {e}")
            raise

    def connect(self):
        """Re-open the port if it was closed."""
        if not self.serial.is_open:
            self.serial.open()

    def close(self):
        """Close the serial port."""
        if self.serial and self.serial.is_open:
            self.serial.close()

    # Encoding

    @staticmethod
    def encode_command(cmd: Command) -> str:
        return (
            COMMAND_PREFIX
            + cmd.command
            + COMMAND_DELIMITER
            + json.dumps(cmd.parameters)
            + COMMAND_TERMINATOR
        )

    @staticmethod
    def decode_command(command: bytes) -> Command:
        """
        Parse a raw command/response frame into a Command.
        Raises ValueError for any frame that is not a well-formed command frame.
        Binary sensor frames must NOT be passed here – they belong in handle_data().
        """
        command_str = command.decode().strip()

        if not command_str.startswith(COMMAND_PREFIX) or not command_str.endswith(
            COMMAND_TERMINATOR
        ):
            raise ValueError(f"Not a command frame: {command_str!r}")

        body = command_str[len(COMMAND_PREFIX) : -len(COMMAND_TERMINATOR)]
        # Split on the *first* delimiter only – JSON values may contain COMMAND_DELIMITER
        parts = body.split(COMMAND_DELIMITER, 1)

        if len(parts) == 1:
            return Command(command=parts[0], parameters={})

        command_name = parts[0]
        parameters = json.loads(parts[1])
        return Command(command=command_name, parameters=parameters)

    # I/O helpers

    def _sync_send(self, data: str):
        self.serial.write(data.encode())

    async def async_send(self, data: str):
        """Non-blocking send – offloads the blocking write to a thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_send, data)

    async def async_send_command(self, cmd: Command):
        await self.async_send(self.encode_command(cmd))

    # Sensor state

    def handle_data(self, data: bytearray | bytes):
        """Unpack a binary sensor frame and update in-memory state."""
        parsed = unpack(data)

        if "pir" in parsed:
            self._pir = parsed["pir"]
        if "phc" in parsed:
            self._phc = parsed["phc"]
        if "radar" in parsed:
            self._radar = parsed["radar"]
        if "threat" in parsed:
            self._threat = parsed["threat"]
            self.last_sync = int(time.time())
        if "sleep_ms" in parsed:
            self._sleep_ms = parsed["sleep_ms"]
        if "volt" in parsed:
            self._volt = parsed["volt"]
        if "tte_s" in parsed:
            self._tte_s = parsed["tte_s"]

    def status(self) -> StatusResponse:
        s_i = self._sleep_ms if self._sleep_ms else 0
        t_s = 0.0
        t_h = 0.0
        p_h = "UNKNOWN"

        if self._threat:
            t_s = self._threat["score"]
            t_h = self._threat["threshold"]
            p_h = self._threat["phase"]

        v_t = self._volt["value"] if self._volt else 0.0
        tte = self._tte_s if self._tte_s else 0

        return StatusResponse(
            sleepInterval=s_i,
            lastSync=self.last_sync,
            threatScore=t_s,
            threshold=t_h,
            phase=p_h,
            volt=v_t,
            tte_s=tte,
        )

    def sensors(self) -> list[SensorResponse]:
        r = []

        if self._pir:
            r.append(
                SensorResponse(
                    name="Pir Motion",
                    value=str(self._pir["value"]),
                    timestamp=self._pir["ts"],
                )
            )

        if self._phc:
            r.append(
                SensorResponse(
                    name="Photo Resistor",
                    value=f"{self._phc['value']:.4f}",
                    timestamp=self._phc["ts"],
                )
            )

        if self._radar:
            r.append(
                SensorResponse(
                    name="Radar",
                    value=f"{self._radar['distance']}cm | {self._radar['energy']}%",
                    timestamp=self._radar["ts"],
                )
            )

        return r

    # Background reader

    async def background_reader(self):
        """
        Sole consumer of the serial port.

        Routing:
          cmd:…:end frames  →  _cmd_queue   (picked up by _request / log_download)
          everything else   →  handle_data()
        """
        loop = asyncio.get_event_loop()
        while True:
            try:
                raw = await loop.run_in_executor(None, self.serial.readline)
                if raw:
                    if raw.lstrip().startswith(COMMAND_PREFIX.encode()):
                        # Route command/response frame to whoever is waiting
                        await self._cmd_queue.put(raw)
                    else:
                        # Binary sensor frame
                        self.handle_data(raw)
            except (ValueError, SerialException) as e:
                print(f"[ESPUart] reader error: {e}")
            except Exception as e:
                print(f"[ESPUart] reader unexpected error: {e}")
            await asyncio.sleep(0)

    # Request / response

    async def _request(
        self,
        cmd: Command,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Send *cmd*, wait for the next command frame from the queue, decode it,
        and return its parameters dict.
        Raises asyncio.TimeoutError on timeout, ValueError on a bad frame.
        """
        await self.async_send_command(cmd)
        raw = await asyncio.wait_for(self._cmd_queue.get(), timeout=timeout)
        response = self.decode_command(raw)
        return response.parameters

    async def uart_ping(self, timeout: float = DEFAULT_TIMEOUT) -> UartPingResponse:
        """
        Two-stage UART health check:
          1. Is the serial port open?       (software layer)
          2. Does the peer ACK a UAK frame? (physical/wiring layer)

          'ok'          – port open AND peer responded
          'unconnected' – port open but no response (wiring, dead device)
          'error'       – port not open or not initialised
        """
        if self.serial is None or not self.serial.is_open:
            return UartPingResponse(status="error")

        try:
            params = await self._request(
                Command(command=CMD_UART_ACK, parameters={}),
                timeout=timeout,
            )
            if params.get("status") == "ok":
                return UartPingResponse(status="ok")
            return UartPingResponse(status="error")
        except asyncio.TimeoutError:
            return UartPingResponse(status="unconnected")
        except ValueError, KeyError, SerialException:
            return UartPingResponse(status="error")

    async def ping(self, timeout: float = DEFAULT_TIMEOUT) -> PingResponse:
        """
        PNG → PingResponse(status='ok' | 'error' | 'unconnected')
        """
        try:
            params = await self._request(
                Command(command=CMD_PING, parameters={}),
                timeout=timeout,
            )
            return PingResponse(status=params.get("status", "error"))
        except asyncio.TimeoutError:
            return PingResponse(status="unconnected")
        except ValueError, KeyError, SerialException:
            return PingResponse(status="error")

    async def log_info(self, timeout: float = DEFAULT_TIMEOUT) -> LogInfoResponse:
        """LGI → LogInfoResponse"""
        params = await self._request(
            Command(command=CMD_LOG_INFO, parameters={}),
            timeout=timeout,
        )
        return LogInfoResponse(**params)

    async def log_download(
        self,
        log_id: int,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[LogInfoResponse]:
        """
        LGD → list[LogInfoResponse]

        Reads frames from _cmd_queue until a terminator (frame with no 'id' key)
        or until the timeout elapses with no new frame.
        """
        await self.async_send_command(
            Command(command=CMD_LOG_DOWNLOAD, parameters={"id": str(log_id)})
        )

        entries: list[LogInfoResponse] = []
        while True:
            try:
                raw = await asyncio.wait_for(self._cmd_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                break

            if not raw:
                break

            cmd = self.decode_command(raw)
            if "id" not in cmd.parameters:
                break  # sentinel / terminator frame

            entries.append(LogInfoResponse(**cmd.parameters))

        return entries

    # Utility

    @staticmethod
    def _safe_dict_eval(dict_str: str) -> dict:
        """
        Parse a simple stringified dict without using eval().
        Supports only string keys and string values.
        e.g. "{'key': 'value', 'foo': 'bar'}"
        """
        out = {}
        if not (dict_str.startswith("{") and dict_str.endswith("}")):
            raise ValueError(f"Invalid dictionary format: {dict_str!r}")
        dict_str = dict_str[1:-1]
        if not dict_str.strip():
            return out
        for item in dict_str.split(","):
            key, _, value = item.partition(":")
            key = key.strip().strip("'\"")
            value = value.strip().strip("'\"")
            out[key] = value
        return out
