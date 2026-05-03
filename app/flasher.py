"""esptool wrapper — flash Edge-Node firmware in background thread."""
from __future__ import annotations

import io
import os
import sys
import threading

from app.config import FIRMWARE_DIR
from app.utils import log


class Flasher:
    def __init__(self, ws_manager=None):
        self.ws = ws_manager
        self._thread: threading.Thread | None = None
        self.running = False
        self.progress = 0
        self.status = "idle"
        self.error = ""

    def start(self, port: str, firmware_dir: str = FIRMWARE_DIR) -> bool:
        if self.running:
            return False

        bootloader = os.path.join(firmware_dir, "bootloader.bin")
        partitions = os.path.join(firmware_dir, "partitions.bin")
        firmware = os.path.join(firmware_dir, "firmware.bin")

        for f in [bootloader, partitions, firmware]:
            if not os.path.exists(f):
                self.error = f"Missing: {os.path.basename(f)}"
                return False

        self._thread = threading.Thread(
            target=self._run, args=(port, bootloader, partitions, firmware), daemon=True
        )
        self._thread.start()
        return True

    def _run(self, port: str, bootloader: str, partitions: str, firmware: str):
        self.running = True
        self.progress = 0
        self.status = "flashing"
        self.error = ""
        self._broadcast()

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        captured = io.StringIO()

        try:
            import esptool
            sys.stdout = captured
            sys.stderr = captured

            esptool.main([
                "--port", port,
                "--baud", "921600",
                "--chip", "esp32s3",
                "write_flash",
                "0x0000", bootloader,
                "0x8000", partitions,
                "0x10000", firmware,
            ])
            self.status = "done"
            self.progress = 100
        except SystemExit as e:
            if e.code == 0:
                self.status = "done"
                self.progress = 100
            else:
                self.status = "error"
                self.error = captured.getvalue()[-300:]
        except Exception as e:
            self.status = "error"
            self.error = str(e)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.running = False
            self._broadcast()
            log(f"[Flash] {self.status}: {self.error or 'OK'}")

    def _broadcast(self):
        if self.ws:
            self.ws.broadcast("flash", {
                "running": self.running,
                "progress": self.progress,
                "status": self.status,
                "error": self.error,
            })

    @staticmethod
    def list_firmware(firmware_dir: str = FIRMWARE_DIR) -> list[dict]:
        if not os.path.isdir(firmware_dir):
            return []
        files = []
        for name in ["bootloader.bin", "partitions.bin", "firmware.bin"]:
            path = os.path.join(firmware_dir, name)
            if os.path.exists(path):
                files.append({"name": name, "size": os.path.getsize(path)})
        return files
