import asyncio
import json
import struct
import time

from serial import Serial
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

# ── Protocol flags (must match sender) ───────────────────────────────────────
FLAG_PIR = 0x01
FLAG_PHC = 0x02
FLAG_RADAR = 0x04
FLAG_THREAT = 0x08
FLAG_SLEEP = 0x10
FLAG_VOLT = 0x20
FLAG_TTE = 0x40

PKT_VERSION = 1
# constants at top (same as sender)
_PHASE_DEC = {0: "DAY", 1: "DUSK", 2: "NIGHT"}


def unpack(buf):
    """Gateway-side unpack. Returns a dict of present fields."""
    version, flags, base_ts = struct.unpack_from(">BBL", buf, 0)
    offset = 6
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
        self.last_sync = 0

    def init(self):
        """Open the serial port. Call once before anything else."""
        try:
            self.serial = Serial(self.port, self.baudrate, timeout=self.timeout)
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

    @staticmethod
    def encode_command(cmd: Command) -> str:
        return (
            COMMAND_PREFIX
            + cmd.command
            + COMMAND_DELIMITER
            + json.dumps(cmd.parameters)
            + COMMAND_TERMINATOR
        )

    def send(self, data: str):
        self.serial.write(data.encode())

    def receive(self) -> bytes:
        return self.serial.readline()

    async def async_send(self, data: str):
        """Non-blocking send – offloads the blocking write to a thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.send, data)

    async def async_receive(self) -> bytes:
        """Non-blocking receive – offloads the blocking readline to a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.receive)

    def handle_data(self, data: bytearray | bytes):
        parsed = unpack(data)

        if "pir" in parsed:
            self._pir = parsed["pir"]
        if "phc" in parsed:
            self._phc = parsed["phc"]
        if "radar" in parsed:
            self._radar = parsed["radar"]
        if "threat" in parsed:
            self._threat = parsed["threat"]
            self.last_sync = time.time()
        if "sleep_ms" in parsed:
            self._sleep_ms = parsed["sleep_ms"]
        if "volt" in parsed:
            self._volt = parsed["volt"]
        if "tte_s" in parsed:
            self._tte_s = parsed["tte_s"]

    def status(self) -> StatusResponse:
        s_i = self._sleep_ms if self._sleep_ms else 0
        t_s = 0
        t_h = 0
        p_h = "UNKNOWN"

        _th = self._threat

        if _th:
            t_s = _th["score"]
            t_h = _th["threshold"]
            p_h = _th["phase"]

        v_t = 0.0
        tte = 0
        if self._volt:
            v_t = self._volt["value"]

        if self._tte_s:
            tte = self._tte_s

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

        pir = self._pir
        if pir:
            r.append(
                SensorResponse(
                    name="Pir Motion", value=str(pir["value"]), timestamp=pir["ts"]
                )
            )

        phc = self._phc
        if phc:
            r.append(
                SensorResponse(
                    name="Photo Resistor",
                    value=f"{phc['value']:.4f}",
                    timestamp=phc["ts"],
                )
            )

        rad = self._radar
        if rad:
            r_str = f"{rad['distance']}cm | {rad['energy']}%"
            r.append(SensorResponse(name="Radar", value=r_str, timestamp=rad["ts"]))

        return r

    def decode_command(self, command: bytes) -> Command:
        """
        Parse a raw wire frame back into a Command.
        Raises ValueError for malformed frames.
        """

        command_str = command.decode().strip()

        if not command_str.startswith(COMMAND_PREFIX) or not command_str.endswith(
            COMMAND_TERMINATOR
        ):
            self.handle_data(command)

        start = command_str.find(COMMAND_PREFIX)
        end = command_str.find(COMMAND_TERMINATOR)
        if start != -1 and end != -1:
            command_str = command_str[start : end + len(COMMAND_TERMINATOR)]

        body = command_str[len(COMMAND_PREFIX) : -len(COMMAND_TERMINATOR)]
        parts = body.split(COMMAND_DELIMITER)

        # No parameters supplied
        if len(parts) == 1:
            return Command(command=parts[0], parameters={})

        command_name = parts[0]
        parameters_str = parts[1]
        parameters = json.loads(parameters_str)
        return Command(command=command_name, parameters=parameters)

    @staticmethod
    def _safe_dict_eval(dict_str: str) -> dict:
        """
        Parse a simple stringified dict without using eval().
        Supports only string keys and string values.
        e.g.  "{'key': 'value', 'foo': 'bar'}"
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

    async def background_reader(self):
        """Continuously drain the serial port and update sensor state."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                raw = await loop.run_in_executor(None, self.serial.readline)
                if raw:
                    # Binary sensor frames don't start with "cmd:" — route accordingly
                    if raw.startswith(COMMAND_PREFIX.encode()):
                        pass  # command/response traffic, ignore here
                    else:
                        self.handle_data(raw)
            except Exception as e:
                print(f"[ESPUart] reader error: {e}")
            await asyncio.sleep(0)

    async def async_send_command(self, cmd: Command):
        await self.async_send(self.encode_command(cmd))

    async def async_receive_command(self) -> Command:
        """
        Await the next complete command frame from the peer and decode it.
        Useful on the receiving MCU side or for listening to unsolicited frames.
        """
        while True:
            raw = await self.async_receive()
            if not raw:  # empty line / keepalive – skip
                continue
            return self.decode_command(raw)

    async def _request(
        self,
        cmd: Command,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Send *cmd*, wait for one response frame, and return its parameters dict.
        Raises asyncio.TimeoutError on timeout, ValueError on bad frame.
        """
        await self.async_send_command(cmd)
        raw = await asyncio.wait_for(self.async_receive(), timeout=timeout)
        response = self.decode_command(raw)
        return response.parameters

    async def uart_ping(self, timeout: float = DEFAULT_TIMEOUT) -> UartPingResponse:
        """
        Two-stage UART health check:
          1. Is the serial port open?          (software layer)
          2. Does the peer ACK a UAK frame?    (physical/wiring layer)

          'ok'          – port open AND peer responded
          'unconnected' – port open but peer didn't respond (wrong wiring, dead device)
          'error'       – port not open or never initialised
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
        except ValueError, KeyError:
            return UartPingResponse(status="error")

    async def ping(self, timeout: float = DEFAULT_TIMEOUT) -> PingResponse:
        """
        PNG  →  PingResponse(status='ok' | 'error' | 'unconnected')

        Quick liveness probe.  The remote MCU should reply with a frame whose
        parameters contain  {'status': 'ok'}  on success.
        """
        try:
            params = await self._request(
                Command(command=CMD_PING, parameters={}),
                timeout=timeout,
            )
            return PingResponse(status=params.get("status", "error"))
        except asyncio.TimeoutError:
            return PingResponse(status="unconnected")
        except ValueError, KeyError:
            return PingResponse(status="error")

    async def log_info(self, timeout: float = DEFAULT_TIMEOUT) -> LogInfoResponse:
        """
        LGI  →  LogInfoResponse

        Returns log metadata: id, source and coverage string.
        """
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
        LGD  →  list[LogInfoResponse]

        Streams the full log for *log_id* from the ESPNOW-MCU.
        Each newline-terminated frame is decoded and collected until the peer
        sends an empty/terminator frame (no 'id' key in parameters).
        """
        await self.async_send_command(
            Command(command=CMD_LOG_DOWNLOAD, parameters={"id": str(log_id)})
        )

        entries: list[LogInfoResponse] = []
        while True:
            try:
                raw = await asyncio.wait_for(self.async_receive(), timeout=timeout)
            except asyncio.TimeoutError:
                break  # no more frames within the window – treat as end of stream

            if not raw:
                break  # empty frame signals end of transmission

            cmd = self.decode_command(raw)
            if "id" not in cmd.parameters:
                break  # sentinel / terminator frame

            entries.append(LogInfoResponse(**cmd.parameters))

        return entries
