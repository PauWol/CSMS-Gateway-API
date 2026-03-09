# Gateway API – CSMS Backend

A FastAPI-based web interface for the Coop Security & Monitoring System (CSMS). This backend communicates with an ESP32 over UART, which in turn connects to security nodes via ESP-NOW mesh networking. Runs on a Raspberry Pi gateway to enable remote monitoring and management of your coop's security system.

## Features

- **Device Monitoring** – Check device liveness, status, and sensor data via REST API
- **Log Management** – Retrieve, stream, and convert binary logs to readable formats (CSV, JSON)
- **Hardware Integration** – Async UART communication with ESP32 at 115200 baud
- **ESP-NOW Support** – Interfaces with ESP32 mesh network connected to security nodes
- **Static Web Server** – Serves a Vue.js-based SPA frontend with compiled CSS/JS
- **Type-Safe API** – Pydantic models for request/response validation

## Tech Stack

**Backend:** Python 3.14+, FastAPI, PySerial (async)
**Frontend:** Vue.js, Vite, Tailwind CSS
**Hardware:** ESP32 (UART gateway), Raspberry Pi (server), Security nodes (ESP-NOW mesh)
**Package Manager:** UV

## Quick Setup

### Automated Setup (Linux-based OS only)

Run the setup script to automatically install and configure everything:

```bash
chmod +x setup.sh && ./setup.sh
```

This script handles dependency installation, environment configuration, and initial setup.

> **Note:** `setup.sh` is designed for Linux-based operating systems (Linux, Raspberry Pi OS, etc.). For other platforms, use the manual setup below.

### Manual Setup

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Configure environment** (`.env`):
   ```env
   RPI_PORT=/dev/serial0           # Serial port on Raspberry Pi
   ESP_BAUDRATE=115200             # Communication speed
   ESP_TIMEOUT=1                   # UART timeout (seconds)
   ```

3. **Run the server:**
   ```bash
   uv run start.py
   ```

The API will be available at `http://localhost:8000` with interactive docs at `/docs`.

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/ping` | Check device liveness |
| `GET /api/status` | Get device status (wake time, sleep interval, threat score) |
| `GET /api/sensors` | Fetch latest sensor readings |
| `GET /api/log-info` | Retrieve log metadata |
| `GET /api/log-download/{log_id}` | Download binary logs |

## Project Structure

```
├── main.py              # FastAPI app and route definitions
├── esp.py               # ESP32 UART communication wrapper
├── models.py            # Pydantic data models
├── pyproject.toml       # Project metadata and dependencies
├── util/
│   └── log_conv.py      # Binary log converter (CLI + module)
└── www/                 # Static frontend assets
```

## Communication Architecture

- **Raspberry Pi** ↔ **ESP32** (UART connection)
- **ESP32** ↔ **Security Nodes** (ESP-NOW mesh network)

Devices communicate via UART using a framing protocol: `cmd:COMMAND;{params}:end`

Example: `cmd:STATUS;:end`

## License

Licensed under AGPL-3.0-or-later © 2026 PauWol