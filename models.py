from pydantic import BaseModel
from typing import Literal

class UartPingResponse(BaseModel):
    status: Literal["ok","error","unconnected"]

class StatusResponse(BaseModel):
    nextWake: int
    sleepInterval: int
    lastSync: int
    threatScore: int


class SensorResponse(BaseModel):
    name: str
    value: str
    timestamp: int


class LogInfoResponse(BaseModel):
    id: int
    source: str
    coverage: str


class PingResponse(BaseModel):
    status: Literal["ok", "error", "unconnected"]


class Command(BaseModel):
    command: str
    parameters: dict

    def __str__(self):
        return f"Command(command={self.command}, parameters={self.parameters})"

    def __repr__(self):
        return self.__str__()
    