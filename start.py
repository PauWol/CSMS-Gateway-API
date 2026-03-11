import uvicorn
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("start")

REQUIRED_ENV = ["RPI_PORT", "ESP_BAUDRATE", "ESP_TIMEOUT"]

def validate_env():
    missing = [key for key in REQUIRED_ENV if not os.getenv(key)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        log.error("Create a .env file or export them before starting.")
        sys.exit(1)

HOST = os.getenv("HOST",    "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
RELOAD = os.getenv("ENV", "production").lower() == "development"
WORKERS = 1   # must stay 1 -> serial port cannot be shared across workers

if __name__ == "__main__":
    validate_env()

    log.info("Starting Gateway API on http://%s:%d  (reload=%s)", HOST, PORT, RELOAD)

    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=RELOAD,
        workers=WORKERS,
        log_level="info",
    )