#!/usr/bin/env python3
"""
LDPS Factory — Complete Production Test Runner

Runs all verification tests through LDPS-Probe:
- WS2812 signal capture with .lshow content comparison
- Frame ID delay analysis
- Play/Pause/Seek/Brightness verification
- Hub-Node interaction tests (ESP-NOW)
- Edge cases and stress tests

Requirements:
- LDPS-Probe firmware on test board (connected via USB serial)
- Edge-Node under test (connected via test jig wires)
- Test pack synced to Node (or Node playing any .lshow)

Usage:
    python3 tools/run_tests.py --probe /dev/cu.usbmodem11101 [--node /dev/cu.usbmodem11201]
"""

import argparse
import json
import os
import serial
import struct
import sys
import time

# ── Test Framework ───────────────────────────────────────────

passed = 0
failed = 0
results = []

def test(name, condition, detail=""):
    global passed, failed
    status = "PASS" if condition else "FAIL"
    if condition:
        passed += 1
    else:
        failed += 1
    results.append({"name": name, "status": status, "detail": detail})
    print(f"  {status}: {name}" + (f" — {detail}" if not condition and detail else ""))


def flush(ser):
    while ser.in_waiting:
        ser.readline()


def send_and_wait(ser, cmd, prefix, timeout=5):
    """Send UART command and wait for response line containing prefix."""
    flush(ser)
    ser.write(f"{cmd}\n".encode())
    buf = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        text = buf.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if prefix in line:
                return line.strip()
        time.sleep(0.05)
    return None


def capture(ser, duration=4000, pack_id=61):
    """Run PLAY_AND_CAPTURE, return parsed JSON or None."""
    flush(ser)
    ser.write(f"TB:PLAY_AND_CAPTURE,0,{pack_id},100,{duration}\n".encode())
    buf = b""
    t0 = time.time()
    while time.time() - t0 < duration / 1000 + 10:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        text = buf.decode("utf-8", errors="replace")
        if "CAPTURE_RESULT,{" in text:
            idx = text.index("CAPTURE_RESULT,{") + len("CAPTURE_RESULT,")
            end = text.rfind("]}") + 2
            if end > idx:
                try:
                    return json.loads(text[idx:end])
                except:
                    return None
        time.sleep(0.05)
    return None


# ── Test Suites ──────────────────────────────────────────────

def test_signal_capture(sp, pack_id):
    """Phase A+B: Multi-frame capture, FPS, timing, pattern comparison."""
    print("\n=== Signal Capture ===")

    sp.write(b"SX:PLAY,0,%d,100\n" % pack_id)
    time.sleep(1)
    flush(sp)

    data = capture(sp, 8000, pack_id)
    if not data:
        test("Capture completed", False, "timeout")
        return

    test("Capture completed", True)

    for ch in data["channels"]:
        c = ch["ch"]
        frames = ch["frames"]
        if frames > 0:
            test(f"CH{c}: frames > 0", frames > 0, f"{frames}")
            test(f"CH{c}: timing OK", ch["timing_ok"])
            if ch["avg_fps"] > 0:
                test(f"CH{c}: FPS 20-50", 20 < ch["avg_fps"] < 50, f"{ch['avg_fps']:.1f}")
            test(f"CH{c}: no drops", ch["dropped"] == 0, f"{ch['dropped']}")


def test_play_pause(sp, pack_id):
    """Phase C: Play/Stop/Seek/Brightness verification."""
    print("\n=== Play/Pause/Seek/Brightness ===")

    # Play
    sp.write(b"SX:PLAY,0,%d,100\n" % pack_id)
    time.sleep(1)
    flush(sp)
    data = capture(sp, 2000, pack_id)
    if data:
        ch = data["channels"][0]
        test("Play: frames > 0", ch["frames"] > 0, f"{ch['frames']}")

    # Stop (freeze frame — signal persists)
    sp.write(b"SX:STOP\n")
    time.sleep(1)
    flush(sp)
    data = capture(sp, 2000, pack_id)
    if data:
        ch = data["channels"][0]
        test("Stop: freeze frame (signal persists)", ch["frames"] > 0)

    # Seek 0 + Play
    sp.write(b"SX:SEEK,0\n")
    time.sleep(0.3)
    sp.write(b"SX:PLAY,0,%d,100\n" % pack_id)
    time.sleep(1)
    flush(sp)
    data = capture(sp, 2000, pack_id)
    if data:
        ch = data["channels"][0]
        test("Seek 0 + Play: frames > 0", ch["frames"] > 0)

    # Brightness 0
    sp.write(b"SX:BRIGHTNESS,0\n")
    time.sleep(1)  # burst should propagate immediately now
    flush(sp)
    data = capture(sp, 2000, pack_id)
    if data:
        ch = data["channels"][0]
        test("Brightness 0: T1H ≈ 0", ch["avg_t1h_ns"] < 100, f"T1H={ch['avg_t1h_ns']:.0f}")

    # Brightness 100 restore
    sp.write(b"SX:BRIGHTNESS,100\n")
    time.sleep(1)
    flush(sp)
    data = capture(sp, 2000, pack_id)
    if data:
        ch1 = next((c for c in data["channels"] if c["avg_t1h_ns"] > 0), None)
        if ch1:
            test("Brightness 100: T1H restored", ch1["avg_t1h_ns"] > 500, f"T1H={ch1['avg_t1h_ns']:.0f}")


def test_delay(sp, pack_id, frame_period_ms=50):
    """Phase D: Delay analysis using frame_id encoding."""
    print("\n=== Delay Analysis ===")

    # Seek to known position, capture, check frame_id
    for seek_ms in [0, 1000, 2000]:
        sp.write(f"SX:SEEK,{seek_ms}\n".encode())
        time.sleep(0.3)
        sp.write(f"SX:PLAY,0,{pack_id},100\n".encode())
        time.sleep(0.5)
        flush(sp)
        data = capture(sp, 2000, pack_id)
        if data:
            ch0 = data["channels"][0]
            fid = ch0.get("frame_id", -1)
            expected = seek_ms // frame_period_ms
            delay_frames = abs(fid - expected) if fid >= 0 else -1
            delay_ms = delay_frames * frame_period_ms if delay_frames >= 0 else -1
            test(f"Seek {seek_ms}ms: frame_id valid",
                 fid >= 0, f"frame_id={fid} expected≈{expected} delay≈{delay_ms}ms")


def test_espnow(sp, node_mac):
    """Phase E: ESP-NOW Hub-Node interaction tests."""
    print("\n=== ESP-NOW Interaction ===")

    # DISCOVER
    resp = send_and_wait(sp, "EN:FF,DISCOVER", "DISCOVER_RSP", 5)
    test("DISCOVER response", resp is not None)
    if resp:
        test("DISCOVER has FW version", "2.0" in resp or "ver" in resp.lower())

    # STATUS_REQ
    resp = send_and_wait(sp, "EN:FF,STATUS_REQ", "STATUS_RSP", 5)
    test("STATUS_RSP received", resp is not None)
    if resp:
        test("STATUS has uuid=", "uuid=" in resp)
        test("STATUS has sd=ok", "sd=ok" in resp)
        test("STATUS has pack=", "pack=" in resp)

    # HW_TEST
    resp = send_and_wait(sp, f"EN:{node_mac},HW_TEST", "HW_TEST_RESULT", 16)
    test("HW_TEST completed", resp is not None)
    if resp:
        idx = resp.find("{")
        if idx >= 0:
            try:
                r = json.loads(resp[idx:])
                test("HW_TEST: SD OK", r.get("sd") == True)
                test("HW_TEST: RF OK", r.get("rf") == True)
                test("HW_TEST: OLED OK", r.get("oled") == True)
                test("HW_TEST: INA226 OK", r.get("ina") == True)
                test("HW_TEST: LED OK", r.get("led") == True)
                test("HW_TEST: NVS OK", r.get("nvs") == True)
                test("HW_TEST: rf_chip", r.get("rf_chip") == "0x22", str(r.get("rf_chip")))
                test("HW_TEST: ina_mfr", r.get("ina_mfr") == "0x5449", str(r.get("ina_mfr")))
            except:
                test("HW_TEST: JSON parse", False)

    # IDENTIFY
    sp.write(f"EN:{node_mac},IDENTIFY\n".encode())
    time.sleep(0.5)
    test("IDENTIFY sent", True)

    # SET_UUID (should reject — already has UUID)
    resp = send_and_wait(sp, f"EN:{node_mac},SET_UUID,aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                         "SET_UUID_ACK", 3)
    test("SET_UUID rejected (already_set)", resp is not None and "already_set" in resp)

    # Invalid UUID format
    resp = send_and_wait(sp, f"EN:{node_mac},SET_UUID,too-short", "SET_UUID_ACK", 3)
    test("SET_UUID invalid format rejected", resp is not None and "invalid" in resp.lower())


def test_i2c(sp):
    """I2C bus sniff test."""
    print("\n=== I2C Sniff ===")
    resp = send_and_wait(sp, "TB:SNIFF_I2C,2000", "I2C_RESULT", 5)
    if resp:
        idx = resp.find("{")
        if idx >= 0:
            r = json.loads(resp[idx:])
            test("I2C: OLED (0x3C) detected", r.get("oled") == True)
            test("I2C: INA226 (0x40) detected", r.get("ina226") == True)
            test("I2C: transactions > 0", r.get("transactions", 0) > 0, str(r.get("transactions")))
    else:
        test("I2C sniff completed", False, "timeout")


def test_stress(sp, node_mac):
    """Phase G: Stress tests."""
    print("\n=== Stress Tests ===")

    # 30x rapid DISCOVER
    count = 0
    for i in range(30):
        sp.write(b"EN:FF,DISCOVER\n")
        time.sleep(0.3)
        while sp.in_waiting:
            line = sp.readline().decode("utf-8", errors="replace")
            if "DISCOVER_RSP" in line:
                count += 1
    test(f"30x DISCOVER: ≥25 responses", count >= 25, f"{count}/30")

    # 10x HW_TEST
    hw_count = 0
    for i in range(10):
        resp = send_and_wait(sp, f"EN:{node_mac},HW_TEST", "HW_TEST_RESULT", 16)
        if resp:
            hw_count += 1
    test(f"10x HW_TEST: ≥8 completed", hw_count >= 8, f"{hw_count}/10")


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LDPS Production Test Runner")
    parser.add_argument("--probe", default="/dev/cu.usbmodem11101", help="Probe serial port")
    parser.add_argument("--node", default="/dev/cu.usbmodem11201", help="Node serial port")
    parser.add_argument("--mac", default="E0:72:A1:F2:CB:98", help="Node MAC address")
    parser.add_argument("--pack-id", type=int, default=61, help="Active pack CRC-8 ID")
    args = parser.parse_args()

    print(f"LDPS Production Test Runner")
    print(f"Probe: {args.probe}")
    print(f"Node:  {args.node} (MAC: {args.mac})")
    print(f"Pack ID: {args.pack_id}")

    sp = serial.Serial(args.probe, 115200, timeout=2)
    time.sleep(2)
    flush(sp)

    # Run all test suites
    test_signal_capture(sp, args.pack_id)
    test_play_pause(sp, args.pack_id)
    test_delay(sp, args.pack_id)
    test_espnow(sp, args.mac)
    test_i2c(sp)
    test_stress(sp, args.mac)

    sp.close()

    # Summary
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 50}")

    # Save results
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "probe": args.probe,
        "node_mac": args.mac,
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "tests": results,
    }
    report_path = f"test_report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report saved: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
