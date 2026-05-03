"""Centralized application state."""
import threading


class AppState:
    def __init__(self):
        # Dongle / Test Board
        self.dongle = None
        self.espnow = None
        self.dongle_port = ""
        self.dongle_connected = False
        self.dongle_ready = False

        # Cloud
        self.cloud_url = ""
        self.cloud_token = ""
        self.cloud_email = ""

        # Discovered nodes
        self.discovered_nodes: dict = {}  # mac → {fw, uuid, mac, rssi, last_seen}

        # Provision workflow
        self.wizard_step = "idle"
        self.wizard_mac = ""
        self.wizard_uuid = ""

        # Flash
        self.flash_running = False
        self.flash_progress = 0
        self.flash_status = ""
        self.flash_error = ""

        # Stats
        self.stats_provisioned = 0
        self.stats_failed = 0

        # WebSocket manager
        self.ws = None

        # Threading
        self.stop_event = threading.Event()
