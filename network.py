import subprocess
import sys
import time
import logging
import yaml
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "wifi_manager.log"),
    ],
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "wifi_networks.yaml"
INTERFACE = "wlan0"
AP_CON_NAME = "pi-hotspot"
SCAN_RETRIES = 3


def run(cmd: list[str], check=False) -> subprocess.CompletedProcess:
    log.debug(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def scan_available_networks() -> dict[str, int]:
    """Returns {ssid: signal_strength} for visible networks."""
    # Force a fresh scan
    run(["nmcli", "device", "wifi", "rescan", "ifname", INTERFACE])
    time.sleep(3)  # give scan time to complete

    result = run(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"])
    networks: dict[str, int] = {}

    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 2:
            ssid = parts[0].strip()
            try:
                signal = int(parts[1].strip())
            except ValueError:
                signal = 0
            # Keep highest signal if SSID appears multiple times (multiple APs)
            if ssid and (ssid not in networks or signal > networks[ssid]):
                networks[ssid] = signal

    log.info(f"Found {len(networks)} networks: {networks}")
    return networks


def connection_exists(ssid: str) -> bool:
    result = run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    return ssid in result.stdout.splitlines()


def connect_to_network(ssid: str, password: str) -> bool:
    log.info(f"Attempting to connect to '{ssid}'...")

    # If connection profile exists, just bring it up
    if connection_exists(ssid):
        result = run(["nmcli", "connection", "up", ssid])
    else:
        result = run([
            "nmcli", "device", "wifi", "connect", ssid,
            "password", password,
            "ifname", INTERFACE,
        ])

    if result.returncode == 0:
        log.info(f"✓ Connected to '{ssid}'")
        return True

    log.warning(f"✗ Failed to connect to '{ssid}': {result.stderr.strip()}")
    return False


def delete_ap_if_exists():
    result = run(["nmcli", "connection", "show", AP_CON_NAME])
    if result.returncode == 0:
        run(["nmcli", "connection", "delete", AP_CON_NAME])


def create_ap(ssid: str, password: str) -> bool:
    log.info(f"Creating Access Point '{ssid}'...")
    delete_ap_if_exists()

    result = run([
        "nmcli", "device", "wifi", "hotspot",
        "ifname", INTERFACE,
        "con-name", AP_CON_NAME,
        "ssid", ssid,
        "password", password,
    ])

    if result.returncode == 0:
        log.info(f"✓ AP '{ssid}' is up — connect at 192.168.4.1")
        return True

    log.error(f"✗ Failed to create AP: {result.stderr.strip()}")
    return False


def main():
    config = load_config()
    known_networks: list[dict] = config["networks"]
    ap_config: dict = config["ap"]

    log.info("=== WiFi Manager starting ===")

    available = {}
    for attempt in range(SCAN_RETRIES):
        available = scan_available_networks()
        if available:
            break
        log.warning(f"Scan attempt {attempt + 1}/{SCAN_RETRIES} returned nothing, retrying...")
        time.sleep(2)

    # Find known networks that are visible, sorted by signal strength
    candidates = [
        (net["ssid"], net["password"], available[net["ssid"]])
        for net in known_networks
        if net["ssid"] in available
    ]
    candidates.sort(key=lambda x: x[2], reverse=True)  # strongest first

    if candidates:
        log.info(f"Known networks in range: {[(s, sig) for s, _, sig in candidates]}")
        for ssid, password, signal in candidates:
            if connect_to_network(ssid, password):
                sys.exit(0)
        log.warning("All known networks failed to connect.")
    else:
        log.info("No known networks in range.")

    # Fallback: spin up AP
    if not create_ap(ap_config["ssid"], ap_config["password"]):
        log.critical("Could not create AP either. Check hardware/driver.")
        sys.exit(1)


if __name__ == "__main__":
    main()