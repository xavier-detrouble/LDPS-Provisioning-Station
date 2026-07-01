# LDPS Provisioning-Station

The factory production-line tool. One app, **multiple roles** — it provisions and
QC-tests devices on the assembly line, talking to the Cloud with a **manufacturer
API key** (never Supabase/Studio auth). Separate git repo
(`xavier-detrouble/LDPS-Provisioning-Station`); direct-push to `main` with records.

> Language: English-primary; supplementary Mandarin (Traditional) in marked bullets only, never
> Cantonese, never mid-paragraph.

> **Keep this lean — agent entry only** (target <200 lines): must-see hard rules + build/run +
> pointers to `docs/`. Detail goes in `docs/`, not here.

## Roles per device type (one app, many roles — different job per device)

| Device | What the Station does | Transport |
|---|---|---|
| **Edge-Node** (ESP32-S3) | flash firmware (`flasher.py`, esptool + SHA-256 manifest) **and** provision: cloud `request-uuid` (UUID + Ed25519 genuineness sig + key_id + **recovery key**) → write identity over USB (`P <uuid> <sig> <key_id>`, write-once) → cloud `confirm` → RF/playback QC | **USB serial** (identity is USB-only, never RF) |
| **Control-Hub** (Orange Pi) | functional-test + register + write the SD binding (**flow B**, HUB_IDENTITY §5): read cpuid → cloud `/provision/hub` → write `hub_boot_identity.json`+`signing_keys.json` to the SD → QC → `/provision/hub/confirm`. The Station does **not** flash the OPi | the §6.1 **provisioning channel** (direct-local USB-gadget/eth HTTP — step-3, real-HW) |
| **Dongle** (ESP32-S3) | the Station does **not** flash it directly — it commands the **OPi (hub)** to flash the dongle on the OPi's USB | via the hub |

## Security model (factory access)

- Identity is minted+signed **only by the Cloud** (manufacturer API key gates it; the
  private key is cloud-only, never on the Station). The device just writes a cloud-signed
  identity it can later verify. See `HUB_IDENTITY_DESIGN.md` §6.1.
- Identity ops are **direct-local (USB / USB-gadget-eth), never over RF/internet** — the
  RF identity-write was deliberately removed (`PROVISION_IDENTITY_OWNERSHIP_DESIGN.md` §3.6).
- A keyless device opens a narrow provisioning channel; a provisioned device requires a
  cloud factory-key (rebind) or a local RMA clear to change identity.

## Stack & layout

FastAPI (`main.py` → `create_app`, port `9000`) + a Vue SPA single template
(`templates/index.html`, `[[ ]]` delimiters; tabs Setup/Dashboard/Provision/History).

```
app/
  cloud_client.py   manufacturer-key cloud client: login, get_quota, request_uuid/confirm,
                    report_test_fail (node) · provision_hub/confirm_hub/rebind_hub (hub)
  routes/
    cloud.py        /api/cloud/login|status|quota|logout
    provision.py    node: discover, identify, hw-test, finalize (USB provision), playback-test,
                    report-fail (ST3 — QC yield gate)
    provision_hub.py hub: /api/hub/provision|confirm|rebind (cloud orchestration behind the GUI)
    flash.py        node firmware flashing · dongle.py · system.py · history.py · ws.py
  flasher.py · node_serial.py · espnow.py · dongle.py   (HW transports)
  provision_log.py  local SQLite yield log (success/failed)
  state.py · ws_manager.py · config.py · utils.py
tools/  run_tests.py · generate_test_pack.py
```

## Dev

```bash
# Local: point at the local cloud + run
CLOUD_URL=http://localhost:3737 PORT=9000 python3 main.py
```
- `CLOUD_URL` env (prod default is the Zeabur cloud); set it for local/testbed.
- Manufacturer API key auth: `X-Manufacturer-Key` (cloud `/provision/login`); separate from
  Studio JWT / Hub device-token. See cloud `docs/adr/ADR-MANUFACTURER-API-KEY-AUTH.md`.
- Verify cloud comms without HW via FastAPI `TestClient` (cloud_client makes real cloud calls).

## Authority docs

- [`../docs/architecture/provisioning/README.md`](../docs/architecture/provisioning/README.md) — **cross-repo MAP** (which repo
  implements which part of the provisioning design; read first).
- `LDPS-Hardware/docs/provisioning/PROVISION_IDENTITY_OWNERSHIP_DESIGN.md` — single authority
  (node identity/ownership/QC; §9 = build log).
- `LDPS-Hardware/docs/provisioning/HUB_IDENTITY_DESIGN.md` — hub flow B (§5), factory-access
  channel (§6.1), the Station/Hub change register (§8.3/§8.4), execution log (§13).

## Status (2026-07-02)

Both device types provision through a one-Start **auto-flow** (not a stepped wizard): an idle
**readiness pre-check** gates Start, then the sequence runs automatically with per-phase progress
+ results; the operator only confirms the node Visual check + handles a failure. Tabs:
`Dashboard · Provision Node · Provision Hub · History`.
- **Provision Node:** Flash → Format SD → Discover (USB) → Hardware test (RF) → Visual
  (auto-IDENTIFY lights the 8 LED ch) → Provision UUID. Node USB port + Test Board (dongle) are
  **auto-detected** (dongle probe `DG:STATUS`→`dg:READY`; node by elimination), dongle auto-reconnects.
  Results → `provision_log` (scoped by `manufacturer_id`) + cloud `node.test_results`.
- **Provision Hub (flow B):** Read cpuid (§6.1) → Push hub program → Flash dongle FW → Sign →
  Write + confirm. Readiness = hub reachable on §6.1 & FRESH·cpuid + Touch-Hub quota.
- On done, a **box label** preview (hi-res QR → public `/verify/<uuid>` + product + UUID + recovery
  key + production date). UI is English-only.

Full as-built detail + real-HW verification: `LDPS-Hardware/docs/architecture/provisioning/HUB_IDENTITY_DESIGN.md`
§13 (2026-07-02 entry) — auto-flow, §6.1 program-status/health-check/Safe-Mode fixes, hub Lock-page
watchdog, verify page + warranty.

**Commit model (2026-07-01):** the Station is the factory authority — **no manual confirm
button**. It writes the identity to the hub (§6.1) / node (USB) and on the device's read-back
success **auto-confirms** to Cloud (commit is server-side idempotent → retry-safe). If the
write/read-back fails the operator gets **Release & start over** (free the quota slot, delete
the reservation) or **Mark defective** (status `defected` → keeps the row + counts the quota
slot for yield/RMA; the manufacturer requests more quota from us with cause). Abandoned
`reserved` rows are reaped server-side after the stale window. Hub routes `/api/hub/{defect}`,
node `/api/provision/defect`; cloud `defect_provision_{hub,node}` RPC + widened lifecycle CHECK.
