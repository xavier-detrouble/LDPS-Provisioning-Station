"""Station configuration constants."""
import os

PORT = int(os.environ.get("PORT", "9000"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
TEST_PACK_DIR = os.path.join(STATIC_DIR, "test_pack")
FIRMWARE_DIR = os.path.join(STATIC_DIR, "firmware")
DB_PATH = os.path.join(STATIC_DIR, "provision_log.db")

DEFAULT_CLOUD_URL = "https://ldpstudioc.zeabur.app"
DONGLE_BAUDRATE = 115200

FAKE_UUID = "00000000-0000-4000-a000-000000000000"
TEST_PACK_UUID = "fb000000-0000-4000-a000-000000000001"
