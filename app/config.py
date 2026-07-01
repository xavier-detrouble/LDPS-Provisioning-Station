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
_ENV_CLOUD_URL = os.environ.get("CLOUD_URL", "").strip()
if _ENV_CLOUD_URL:
    DEFAULT_CLOUD_URL = _ENV_CLOUD_URL
elif _STAGE in STAGE_CLOUD_URLS:
    DEFAULT_CLOUD_URL = STAGE_CLOUD_URLS[_STAGE]
else:
    # Fail-fast (Xavier): the Station must NEVER silently default to production. Provisioning
    # against the live cloud burns real quota + writes real recovery keys, so a forgotten stage
    # should STOP startup, not quietly hit prod. To use prod you must say so explicitly.
    raise RuntimeError(
        "LDPS_STAGE (or CLOUD_URL) is required — the Station refuses to default to production.\n"
        "Set one explicitly, e.g.  LDPS_STAGE=local PORT=9000 python3 main.py  (or =uat / =prod).\n"
        "Stages: " + ", ".join(STAGE_CLOUD_URLS) + ".  See ../docs/how-to/STAGE_SWITCH.md."
    )

def _derive_stage(url: str) -> str:
    """Stage label from the resolved Cloud URL — correct even when CLOUD_URL was set directly."""
    import re
    u = (url or "").lower()
    host = u.split("//", 1)[-1]
    if re.search(r"localhost|127\.0\.0\.1|0\.0\.0\.0", u) or re.match(r"(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host):
        return "local"
    if "uat" in u:
        return "uat"
    if "zeabur" in u or "ldpstudioc" in u:
        return "prod"
    return "unknown"

LDPS_STAGE_RESOLVED = _STAGE if _STAGE in STAGE_CLOUD_URLS else _derive_stage(DEFAULT_CLOUD_URL)

# §6.1 hub provisioning channel — where the Station reaches the assembled OPi to read its
# cpuid + write the cloud-signed binding (flow B step-3). Factory transport = USB-gadget/eth
# link-local; during LAN testing = the OPi's LAN address. Set HUB_HOST to switch.
# See HUB_IDENTITY_DESIGN §6.1 + ../docs/how-to/STAGE_SWITCH.md.
HUB_HOST = os.environ.get("HUB_HOST", "http://192.168.8.158:8000").rstrip("/")
DONGLE_BAUDRATE = 115200

FAKE_UUID = "00000000-0000-4000-a000-000000000000"
TEST_PACK_UUID = "fb000000-0000-4000-a000-000000000001"
