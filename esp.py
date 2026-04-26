import asyncio
from serial import Serial
from models import (
    Command,
    PingResponse,
    StatusResponse,
    SensorResponse,
    LogInfoResponse,
)

# Command frame format:
COMMAND_PREFIX     = "cmd:"
COMMAND_DELIMITER  = ";"
COMMAND_TERMINATOR = ":end"

# Command names:
CMD_UART_ACK = "UAK"  # UART physical-layer ack check

CMD_PING         = "PNG"   # Liveness check
CMD_STATUS       = "STS"   # General status report
CMD_SENSORS      = "SNS"   # Read all sensor values
CMD_LOG_INFO     = "LGI"   # Log metadata (size, last entry, …)
CMD_LOG_DOWNLOAD = "LGD"   # Stream full log over UART

DEFAULT_TIMEOUT = 5.0


class ESPUart:
    """
    Async-capable UART wrapper for ESP32 peer-to-peer communication.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: int = 1):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self.serial: Serial | None = None

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

    def encode_command(self, cmd: Command) -> str:
        """
        Serialise a Command to a wire frame.
        e.g.  cmd:STS;{'key':'val'}:end
        """
        return (
            COMMAND_PREFIX
            + cmd.command
            + COMMAND_DELIMITER
            + str(cmd.parameters)
            + COMMAND_TERMINATOR
        )

    def send(self, data: str):
        self.serial.write(data.encode())

    def receive(self) -> str:
        return self.serial.readline().decode().strip()

    async def async_send(self, data: str):
        """Non-blocking send – offloads the blocking write to a thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.send, data)

    async def async_receive(self) -> str:
        """Non-blocking receive – offloads the blocking readline to a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.receive)

    def decode_command(self, command_str: str) -> Command:
        """
        Parse a raw wire frame back into a Command.
        Raises ValueError for malformed frames.
        """

        start = command_str.find(COMMAND_PREFIX)
        end   = command_str.find(COMMAND_TERMINATOR)
        if start != -1 and end != -1:
            command_str = command_str[start:end + len(COMMAND_TERMINATOR)]

        if not command_str.startswith(COMMAND_PREFIX) or not command_str.endswith(COMMAND_TERMINATOR):
            raise ValueError(f"Invalid command frame: {command_str!r}")
        
        if not command_str.startswith(COMMAND_PREFIX) or \
           not command_str.endswith(COMMAND_TERMINATOR):
            raise ValueError(f"Invalid command frame: {command_str!r}")

        body  = command_str[len(COMMAND_PREFIX):-len(COMMAND_TERMINATOR)]
        parts = body.split(COMMAND_DELIMITER)

        # No parameters supplied
        if len(parts) == 1:
            return Command(command=parts[0], parameters={})

        command_name   = parts[0]
        parameters_str = parts[1]
        parameters     = self._safe_dict_eval(parameters_str)
        return Command(command=command_name, parameters=parameters)

    def _safe_dict_eval(self, dict_str: str) -> dict:
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
            key   = key.strip().strip("'\"")
            value = value.strip().strip("'\"")
            out[key] = value
        return out

    async def async_send_command(self, cmd: Command):
        await self.async_send(self.encode_command(cmd))

    async def async_receive_command(self) -> Command:
        """
        Await the next complete command frame from the peer and decode it.
        Useful on the receiving MCU side or for listening to unsolicited frames.
        """
        while True:
            raw = await self.async_receive()
            if not raw:          # empty line / keepalive – skip
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
        except (ValueError, KeyError):
            return PingResponse(status="error")

    async def status(self, timeout: float = DEFAULT_TIMEOUT) -> StatusResponse:
        """
        STS  →  StatusResponse

        Requests a full status snapshot: next wake time, sleep interval,
        last sync timestamp and current threat score.
        """
        params = await self._request(
            Command(command=CMD_STATUS, parameters={}),
            timeout=timeout,
        )
        return StatusResponse(**params)

    async def sensors(self, timeout: float = DEFAULT_TIMEOUT) -> SensorResponse:
        """
        SNS  →  SensorResponse

        Fetches the latest sensor reading (name, value, timestamp).
        """
        params = await self._request(
            Command(command=CMD_SENSORS, parameters={}),
            timeout=timeout,
        )
        return SensorResponse(**params)

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