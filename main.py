from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv
import os

from models import UartPingResponse, PingResponse, StatusResponse, SensorResponse, LogInfoResponse
from esp import ESPUart

load_dotenv()

esp_uart = ESPUart(
    port=os.getenv("RPI_PORT"),
    baudrate=int(os.getenv("ESP_BAUDRATE")),
    timeout=int(os.getenv("ESP_TIMEOUT"))
)


@asynccontextmanager
async def lifespan(app):
    try:
        esp_uart.init()
    except Exception as e:
        print(f"[startup] Serial init failed: {e}")
        # app still starts — uart_ping will return 'error' to the frontend
    yield
    esp_uart.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/uart-ping")
async def uart_ping() -> UartPingResponse:
    return await esp_uart.uart_ping()

@app.get("/api/ping")
async def ping() -> PingResponse:
    return await esp_uart.ping()

@app.get("/api/status")
async def status() -> StatusResponse:
    return await esp_uart.status()

@app.get("/api/sensors")
async def sensor() -> SensorResponse:
    return await esp_uart.sensors()

@app.get("/api/log-info")
async def log_info() -> LogInfoResponse:
    return await esp_uart.log_info()

@app.get("/api/log-download/{log_id}")
async def log_download(log_id: int):
    return await esp_uart.log_download(log_id)  



# serve static assets
app.mount("/assets", StaticFiles(directory="www/assets"), name="assets")

# serve SPA index
@app.get("/")
def index():
    return FileResponse("www/index.html")