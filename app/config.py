"""Station configuration constants."""
import os

PORT = int(os.environ.get("PORT", "9000"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
TEST_PACK_DIR = os.path.join(STATIC_DIR, "test_pack")
FIRMWARE_DIR = os.path.join(STATIC_DIR, "firmware")
DB_PATH = os.path.join(STATIC_DIR, "provision_log.db")

# Cloud target. Precedence: CLOUD_URL env → LDPS_STAGE profile → prod default — so
# dev never silently provisions against production. LDPS_STAGE flips local / uat /
# prod in one var (matches the Hub's switch). Detail: ../docs/how-to/STAGE_SWITCH.md.
# Station uses manufacturer-key auth only, so no Supabase config here (unlike the Hub).
STAGE_CLOUD_URLS = {
    "local-mock": "http://localhost:8001",
    "local": "http://localhost:3737",
    "uat": "https://ldpstudioc-uat.zeabur.app",
    "prod": "https://ldpstudioc.zeabur.app",
}
_STAGE = os.environ.get("LDPS_STAGE", "").strip().lower()
DEFAULT_CLOUD_URL = (os.environ.get("CLOUD_URL") or STAGE_CLOUD_URLS.get(_STAGE)
                     or "https://ldpstudioc.zeabur.app")

# §6.1 hub provisioning channel — where the Station reaches the assembled OPi to read its
# cpuid + write the cloud-signed binding (flow B step-3). Factory transport = USB-gadget/eth
# link-local; during LAN testing = the OPi's LAN address. Set HUB_HOST to switch.
# See HUB_IDENTITY_DESIGN §6.1 + ../docs/how-to/STAGE_SWITCH.md.
HUB_HOST = os.environ.get("HUB_HOST", "http://192.168.8.158:8000").rstrip("/")
DONGLE_BAUDRATE = 115200

FAKE_UUID = "00000000-0000-4000-a000-000000000000"
TEST_PACK_UUID = "fb000000-0000-4000-a000-000000000001"
