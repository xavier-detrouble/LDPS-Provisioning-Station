#!/usr/bin/env python3
"""
LDPS Provisioning Station — Node production tool.

Connects to a Dongle via UART, authenticates with Cloud,
discovers nodes, triggers HW_TEST, requests UUIDs, and writes them.

Usage:
    python station.py --port /dev/cu.usbmodem11101 --cloud https://ldpstudioc.zeabur.app
"""

import argparse
import json
import sys
import time
import threading
from typing import Optional

import httpx
import serial
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

console = Console()

# ── Dongle UART Protocol ────────────────────────────────────

class DongleConnection:
    """Serial connection to LDPS Dongle for ESP-NOW commands."""

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.ser: Optional[serial.Serial] = None
        self._rx_buf = ""
        self._lock = threading.Lock()
        self._callbacks: dict[str, threading.Event] = {}
        self._responses: dict[str, str] = {}
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False

    def connect(self) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self._running = True
            self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._rx_thread.start()
            console.print(f"[green]Connected to Dongle: {self.port}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to connect: {e}[/red]")
            return False

    def close(self):
        self._running = False
        if self.ser:
            self.ser.close()

    def send(self, mac: str, command: str, args: str = "") -> None:
        """Send ESP-NOW command via Dongle UART."""
        msg = f"EN:{mac},{command}"
        if args:
            msg += f",{args}"
        msg += "\n"
        with self._lock:
            if self.ser:
                self.ser.write(msg.encode())

    def send_and_wait(self, mac: str, command: str, args: str = "",
                      response_prefix: str = "", timeout: float = 10.0) -> Optional[str]:
        """Send command and wait for a specific response from the same MAC."""
        key = f"{mac}:{response_prefix}"
        evt = threading.Event()
        self._callbacks[key] = evt
        self._responses[key] = ""

        self.send(mac, command, args)

        if evt.wait(timeout=timeout):
            result = self._responses.pop(key, "")
            self._callbacks.pop(key, None)
            return result

        self._callbacks.pop(key, None)
        self._responses.pop(key, None)
        return None

    def _rx_loop(self):
        """Background thread reading UART responses."""
        while self._running and self.ser:
            try:
                data = self.ser.read(256)
                if not data:
                    continue
                self._rx_buf += data.decode("utf-8", errors="replace")
                while "\n" in self._rx_buf:
                    line, self._rx_buf = self._rx_buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_line(line)
            except Exception:
                if self._running:
                    time.sleep(0.1)

    def _handle_line(self, line: str):
        """Parse upstream ESP-NOW response: en:{mac},{payload}"""
        if not line.startswith("en:"):
            return
        rest = line[3:]
        comma = rest.find(",")
        if comma < 0:
            return
        mac = rest[:comma].upper()
        payload = rest[comma + 1:]

        # Check for matching callbacks
        for key, evt in list(self._callbacks.items()):
            cb_mac, cb_prefix = key.split(":", 1)
            if mac == cb_mac.upper() and payload.startswith(cb_prefix):
                self._responses[key] = payload
                evt.set()
                break


# ── Cloud API Client ────────────────────────────────────────

class CloudClient:
    """HTTP client for LDPS Cloud Provision API."""

    def __init__(self, cloud_url: str):
        self.cloud_url = cloud_url.rstrip("/")
        self.token: Optional[str] = None
        self.email: Optional[str] = None

    def login(self, email: str, password: str) -> bool:
        try:
            r = httpx.post(f"{self.cloud_url}/api/cloud/login",
                           json={"email": email, "password": password}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token") or data.get("access_token")
                self.email = email
                console.print(f"[green]Logged in as {email}[/green]")
                return True
            console.print(f"[red]Login failed: {r.status_code} {r.text}[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Login error: {e}[/red]")
            return False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request_uuid(self, hardware_serial: str, product_type: str = "default",
                     test_results: dict = None) -> Optional[str]:
        """POST /provision/request-uuid — get a new UUID from Cloud."""
        try:
            r = httpx.post(f"{self.cloud_url}/provision/request-uuid",
                           json={
                               "hardware_serial": hardware_serial,
                               "product_type": product_type,
                               "test_results": test_results,
                           },
                           headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get("uuid")
            console.print(f"[red]request-uuid failed: {r.status_code} {r.json()}[/red]")
            return None
        except Exception as e:
            console.print(f"[red]request-uuid error: {e}[/red]")
            return None

    def confirm(self, uuid: str, success: bool = True) -> bool:
        """POST /provision/confirm — confirm UUID write."""
        try:
            r = httpx.post(f"{self.cloud_url}/provision/confirm",
                           json={"uuid": uuid, "success": success},
                           headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def report_test_fail(self, hardware_serial: str, test_results: dict = None,
                         reason: str = "") -> bool:
        """POST /provision/test-fail — report hardware test failure."""
        try:
            r = httpx.post(f"{self.cloud_url}/provision/test-fail",
                           json={
                               "hardware_serial": hardware_serial,
                               "test_results": test_results,
                               "reason": reason,
                           },
                           headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False


# ── Provisioning Flow ───────────────────────────────────────

class ProvisioningStation:
    """Main provisioning workflow."""

    def __init__(self, dongle: DongleConnection, cloud: CloudClient):
        self.dongle = dongle
        self.cloud = cloud
        self.discovered: dict[str, dict] = {}  # mac → {fw, uuid}

    def discover(self) -> dict:
        """Broadcast DISCOVER via Dongle, collect responses for 3 seconds."""
        self.discovered.clear()
        console.print("[cyan]Broadcasting DISCOVER...[/cyan]")

        # Set up collection
        results = {}
        original_handler = self.dongle._handle_line

        def collect_handler(line):
            original_handler(line)
            if not line.startswith("en:"):
                return
            rest = line[3:]
            comma = rest.find(",")
            if comma < 0:
                return
            mac = rest[:comma].upper()
            payload = rest[comma + 1:]
            if payload.startswith("DISCOVER_RSP,"):
                parts = payload.split(",")
                fw = parts[1] if len(parts) > 1 else "?"
                uuid = parts[2] if len(parts) > 2 else ""
                results[mac] = {"fw": fw, "uuid": uuid, "mac": mac}

        self.dongle._handle_line = collect_handler
        self.dongle.send("FF", "DISCOVER")
        time.sleep(3)
        self.dongle._handle_line = original_handler

        self.discovered = results
        return results

    def identify(self, mac: str) -> None:
        """Flash LEDs on a node for visual identification."""
        self.dongle.send(mac, "IDENTIFY")
        console.print(f"[yellow]Identifying {mac} — LED flashing 5s[/yellow]")

    def hw_test(self, mac: str) -> Optional[dict]:
        """Trigger hardware self-test and wait for results."""
        console.print(f"[cyan]Running HW_TEST on {mac}...[/cyan]")

        # Wait for HW_TEST_RESULT (up to 15s — SD speed test takes time)
        result = self.dongle.send_and_wait(mac, "HW_TEST", "",
                                            "HW_TEST_RESULT,", timeout=15)
        if not result:
            console.print("[red]HW_TEST timeout — no result[/red]")
            return None

        # Parse: HW_TEST_RESULT,{json}
        json_str = result[len("HW_TEST_RESULT,"):]
        try:
            return json.loads(json_str)
        except Exception:
            console.print(f"[red]Failed to parse HW_TEST result: {json_str}[/red]")
            return None

    def provision_node(self, mac: str, product_type: str = "default") -> bool:
        """Full provisioning flow for a single node."""
        console.print(Panel(f"Provisioning {mac}", style="bold cyan"))

        # Step 1: Identify
        self.identify(mac)
        if not Confirm.ask("Can you see the node LED flashing?"):
            console.print("[yellow]Skipped — node not visually confirmed[/yellow]")
            return False

        # Step 2: HW Test
        test_results = self.hw_test(mac)
        if test_results is None:
            self.cloud.report_test_fail(mac, reason="HW_TEST timeout")
            return False

        # Display results
        table = Table(title="Hardware Test Results")
        table.add_column("Test", style="bold")
        table.add_column("Result")
        for key, val in test_results.items():
            style = "green" if val is True or (isinstance(val, (int, float)) and val > 0) else "red"
            table.add_row(key, str(val), style=style)
        console.print(table)

        # Check critical tests
        critical_fail = False
        if not test_results.get("sd"):
            console.print("[red]FAIL: SD card not working[/red]")
            critical_fail = True
        if not test_results.get("nvs"):
            console.print("[red]FAIL: NVS not working[/red]")
            critical_fail = True
        if not test_results.get("uuid_empty"):
            console.print("[red]FAIL: Node already has UUID[/red]")
            critical_fail = True

        if critical_fail:
            self.cloud.report_test_fail(mac, test_results, "Critical test failed")
            console.print("[red]Node FAILED — not provisioning[/red]")
            return False

        if not Confirm.ask("Tests passed. Proceed with provisioning?"):
            return False

        # Step 3: Request UUID from Cloud
        console.print("[cyan]Requesting UUID from Cloud...[/cyan]")
        uuid = self.cloud.request_uuid(mac, product_type, test_results)
        if not uuid:
            console.print("[red]Failed to get UUID from Cloud[/red]")
            return False
        console.print(f"[green]Got UUID: {uuid}[/green]")

        # Step 4: Write UUID to Node via ESP-NOW
        console.print(f"[cyan]Writing UUID to node...[/cyan]")
        ack = self.dongle.send_and_wait(mac, "SET_UUID", uuid,
                                         "SET_UUID_ACK,", timeout=5)
        if not ack:
            console.print("[red]SET_UUID timeout[/red]")
            self.cloud.confirm(uuid, success=False)
            return False

        if "ok" in ack:
            console.print(f"[green]UUID written successfully![/green]")
            self.cloud.confirm(uuid, success=True)
            console.print(Panel(f"[bold green]Node {mac} provisioned: {uuid}[/bold green]"))
            return True
        else:
            console.print(f"[red]SET_UUID failed: {ack}[/red]")
            self.cloud.confirm(uuid, success=False)
            return False


# ── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LDPS Provisioning Station")
    parser.add_argument("--port", default="/dev/cu.usbmodem11101",
                        help="Dongle serial port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--cloud", default="https://ldpstudioc.zeabur.app",
                        help="Cloud API URL")
    args = parser.parse_args()

    console.print(Panel("[bold]LDPS Provisioning Station[/bold]", style="blue"))

    # Connect to Dongle
    dongle = DongleConnection(args.port, args.baud)
    if not dongle.connect():
        sys.exit(1)

    # Cloud login
    cloud = CloudClient(args.cloud)
    email = Prompt.ask("Cloud email")
    password = Prompt.ask("Cloud password", password=True)
    if not cloud.login(email, password):
        dongle.close()
        sys.exit(1)

    station = ProvisioningStation(dongle, cloud)
    product_type = Prompt.ask("Product type", default="default")

    # Main loop
    try:
        while True:
            console.print("\n[bold]Commands:[/bold]")
            console.print("  [cyan]d[/cyan] — Discover nodes")
            console.print("  [cyan]p[/cyan] — Provision a node")
            console.print("  [cyan]t[/cyan] — Run HW test only")
            console.print("  [cyan]i[/cyan] — Identify a node")
            console.print("  [cyan]q[/cyan] — Quit")

            cmd = Prompt.ask("Command", choices=["d", "p", "t", "i", "q"])

            if cmd == "q":
                break

            elif cmd == "d":
                nodes = station.discover()
                if not nodes:
                    console.print("[yellow]No nodes found[/yellow]")
                    continue
                table = Table(title=f"Discovered Nodes ({len(nodes)})")
                table.add_column("MAC")
                table.add_column("Firmware")
                table.add_column("UUID")
                table.add_column("Status")
                for mac, info in nodes.items():
                    has_uuid = info["uuid"] and info["uuid"] != ""
                    status = "[green]Has UUID[/green]" if has_uuid else "[yellow]No UUID[/yellow]"
                    table.add_row(mac, info["fw"], info["uuid"][:12] if has_uuid else "-", status)
                console.print(table)

            elif cmd == "p":
                if not station.discovered:
                    console.print("[yellow]Run discover first[/yellow]")
                    continue
                # Show nodes without UUID
                no_uuid = {m: i for m, i in station.discovered.items() if not i.get("uuid")}
                if not no_uuid:
                    console.print("[yellow]No unprovisioned nodes found[/yellow]")
                    continue
                mac = Prompt.ask("Node MAC to provision",
                                 choices=list(no_uuid.keys()))
                station.provision_node(mac, product_type)

            elif cmd == "t":
                mac = Prompt.ask("Node MAC")
                station.hw_test(mac)

            elif cmd == "i":
                mac = Prompt.ask("Node MAC")
                station.identify(mac)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
    finally:
        dongle.close()
        console.print("[dim]Disconnected[/dim]")


if __name__ == "__main__":
    main()
