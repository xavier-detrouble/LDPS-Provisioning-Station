#!/usr/bin/env python3
"""
Generate LDPS Test Pack — 8ch × 680px deterministic .lshow files.

Patterns designed for both machine verification and visual inspection:
  Seq 0: Solid Colors — each channel a unique solid color (3s @20fps)
  Seq 1: Pixel Address — progressive gradient per pixel (5s @20fps)
  Seq 2: Frame Rate — black/white alternating (10s @50fps)

Usage:
    python3 tools/generate_test_pack.py [--output static/test_pack]
"""

import argparse
import hashlib
import json
import os
import struct
import uuid
import zlib

# ── Constants ────────────────────────────────────────────────

PACK_UUID = "fb000000-0000-4000-a000-000000000001"
FAKE_NODE_UUID = "00000000-0000-4000-a000-000000000000"
CHANNELS = 8
PIXELS_PER_CH = 680
COLOR_MODE_RGB = 3

# Solid color per channel (RGB)
CHANNEL_COLORS = [
    (255, 0, 0),      # CH0: Red
    (0, 255, 0),      # CH1: Green
    (0, 0, 255),      # CH2: Blue
    (255, 255, 255),  # CH3: White
    (255, 255, 0),    # CH4: Yellow
    (0, 255, 255),    # CH5: Cyan
    (255, 0, 255),    # CH6: Magenta
    (255, 128, 0),    # CH7: Orange
]

SEQUENCES = [
    {
        "seq_index": 0,
        "name": "Solid Colors",
        "file_uuid": "aa000001-0000-4000-a000-000000000001",
        "sequence_uuid": "bb000001-0000-4000-a000-000000000001",
        "duration_ms": 3000,
        "fps": 20,
        "description": "8ch solid colors 680px",
    },
    {
        "seq_index": 1,
        "name": "Pixel Address",
        "file_uuid": "aa000002-0000-4000-a000-000000000002",
        "sequence_uuid": "bb000002-0000-4000-a000-000000000002",
        "duration_ms": 5000,
        "fps": 20,
        "description": "Progressive gradient",
    },
    {
        "seq_index": 2,
        "name": "Frame Rate Test",
        "file_uuid": "aa000003-0000-4000-a000-000000000003",
        "sequence_uuid": "bb000003-0000-4000-a000-000000000003",
        "duration_ms": 10000,
        "fps": 50,
        "description": "B/W alternate 50fps",
    },
]


# ── .lshow Binary Builder ────────────────────────────────────

def uuid_to_bytes(uuid_str: str) -> bytes:
    return uuid.UUID(uuid_str).bytes


def build_lshow(seq: dict, frame_generator) -> bytes:
    fps = seq["fps"]
    frame_period_ms = 1000 // fps
    total_frames = seq["duration_ms"] // frame_period_ms

    frame_data = bytearray()
    for frame_idx in range(total_frames):
        frame_data.extend(frame_generator(frame_idx, total_frames))

    crc32 = zlib.crc32(frame_data) & 0xFFFFFFFF

    # Header (96 bytes)
    header = bytearray(96)
    header[0:4] = b"LDS1"
    header[4] = 0x01
    header[5] = CHANNELS
    struct.pack_into(">H", header, 6, total_frames)
    struct.pack_into(">H", header, 8, frame_period_ms)
    header[11] = COLOR_MODE_RGB
    struct.pack_into(">I", header, 12, seq["duration_ms"])
    header[16:32] = uuid_to_bytes(seq["file_uuid"])
    header[32:48] = uuid_to_bytes(seq["sequence_uuid"])
    struct.pack_into(">I", header, 48, crc32)
    desc = seq["description"].encode("utf-8")[:31]
    header[56:56 + len(desc)] = desc

    # Channel table (8 × 4 bytes)
    channel_table = bytearray()
    for _ in range(CHANNELS):
        channel_table.extend(struct.pack(">H", PIXELS_PER_CH))
        channel_table.extend(b"\x00\x00")

    return bytes(header) + bytes(channel_table) + bytes(frame_data)


# ── Frame Generators ─────────────────────────────────────────

def _encode_frame_id(frame_idx: int, seq_idx: int) -> bytes:
    """Encode frame index into pixel 0 for delay analysis.
    Pixel 0: R=frame_idx%256, G=frame_idx//256, B=seq_idx"""
    return bytes([frame_idx % 256, frame_idx // 256, seq_idx])


def gen_solid_colors(frame_idx: int, total_frames: int) -> bytes:
    """Seq 0: Each channel a unique solid color. Pixel 0 encodes frame_index."""
    frame = bytearray()
    for ch in range(CHANNELS):
        r, g, b = CHANNEL_COLORS[ch]
        # Pixel 0: frame ID
        frame.extend(_encode_frame_id(frame_idx, 0))
        # Pixel 1-679: solid color
        frame.extend(bytes([r, g, b]) * (PIXELS_PER_CH - 1))
    return bytes(frame)


def gen_pixel_address(frame_idx: int, total_frames: int) -> bytes:
    """Seq 1: Pixel 0=frame ID, rest: R=position%256, G=ch*32, B=frame_idx%256.
    Visual: color gradient wave that shifts over time."""
    frame = bytearray()
    b_val = frame_idx % 256
    for ch in range(CHANNELS):
        # Pixel 0: frame ID
        frame.extend(_encode_frame_id(frame_idx, 1))
        # Pixel 1-679: gradient
        g_val = ch * 32
        for px in range(1, PIXELS_PER_CH):
            r_val = px % 256
            frame.extend(bytes([r_val, g_val, b_val]))
    return bytes(frame)


def gen_framerate_test(frame_idx: int, total_frames: int) -> bytes:
    """Seq 2: Pixel 0=frame ID, rest: even frames=white, odd=black."""
    if frame_idx % 2 == 0:
        pixel = bytes([255, 255, 255])
    else:
        pixel = bytes([0, 0, 0])
    frame = bytearray()
    for ch in range(CHANNELS):
        frame.extend(_encode_frame_id(frame_idx, 2))
        frame.extend(pixel * (PIXELS_PER_CH - 1))
    return bytes(frame)


GENERATORS = [gen_solid_colors, gen_pixel_address, gen_framerate_test]


# ── Expected Pattern Export ──────────────────────────────────

def export_expected_patterns(out_dir: str):
    """Export expected pixel data for Probe comparison (first frame of each sequence)."""
    expected_dir = os.path.join(out_dir, "expected")
    os.makedirs(expected_dir, exist_ok=True)

    for seq, gen_fn in zip(SEQUENCES, GENERATORS):
        frame0 = gen_fn(0, 1)
        # Split into per-channel files
        for ch in range(CHANNELS):
            offset = ch * PIXELS_PER_CH * 3
            ch_data = frame0[offset:offset + PIXELS_PER_CH * 3]
            path = os.path.join(expected_dir, f"seq{seq['seq_index']}_ch{ch}.bin")
            with open(path, "wb") as f:
                f.write(ch_data)

        # Also export as hex for UART command
        hex_path = os.path.join(expected_dir, f"seq{seq['seq_index']}_summary.txt")
        with open(hex_path, "w") as f:
            f.write(f"# {seq['name']} — first frame expected pixels\n")
            f.write(f"# {CHANNELS} channels × {PIXELS_PER_CH} pixels\n\n")
            for ch in range(CHANNELS):
                offset = ch * PIXELS_PER_CH * 3
                first10 = frame0[offset:offset + 30]  # First 10 pixels
                f.write(f"CH{ch}: {first10.hex().upper()}\n")

    print(f"  Expected patterns exported to {expected_dir}")


# ── Pack Metadata ────────────────────────────────────────────

def gen_pack_table() -> dict:
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "packs": [{
            "index": 0,
            "uuid": PACK_UUID,
            "name": "LDPS Production Test Pack",
            "sequence_count": len(SEQUENCES),
            "sync": True,
            "synced_version": "2026-05-03T00:00:00Z",
        }],
    }


def gen_sequences_lut() -> dict:
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "entries": [{
            "seq_index": s["seq_index"],
            "sequence_uuid": s["sequence_uuid"],
            "name": s["name"],
            "time_ms": s["duration_ms"],
        } for s in SEQUENCES],
    }


def gen_assignment(lshow_hashes: dict) -> dict:
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "entries": [{
            "seq_index": s["seq_index"],
            "file_uuid": s["file_uuid"],
            "sequence_uuid": s["sequence_uuid"],
            "sha256": lshow_hashes[s["file_uuid"]],
        } for s in SEQUENCES],
    }


def gen_active() -> dict:
    return {"uuid": PACK_UUID}


def write_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  Written: {os.path.basename(path)}")


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate LDPS Test Pack")
    parser.add_argument("--output", default="static/test_pack")
    args = parser.parse_args()

    out = args.output
    pack_dir = os.path.join(out, PACK_UUID)
    assign_dir = os.path.join(pack_dir, "assignments")
    lshow_dir = os.path.join(out, "lshow")

    os.makedirs(assign_dir, exist_ok=True)
    os.makedirs(lshow_dir, exist_ok=True)

    # Generate .lshow files
    lshow_hashes = {}
    for seq, gen_fn in zip(SEQUENCES, GENERATORS):
        data = build_lshow(seq, gen_fn)
        file_uuid = seq["file_uuid"]
        path = os.path.join(lshow_dir, f"{file_uuid}.lshow")
        with open(path, "wb") as f:
            f.write(data)

        sha = hashlib.sha256(data).hexdigest()
        lshow_hashes[file_uuid] = sha

        frame_period = 1000 // seq["fps"]
        total_frames = seq["duration_ms"] // frame_period
        frame_size = CHANNELS * PIXELS_PER_CH * 3
        file_size = 96 + CHANNELS * 4 + total_frames * frame_size
        print(f"  {seq['name']}: {total_frames} frames @{seq['fps']}fps, "
              f"{PIXELS_PER_CH}px/ch, {file_size:,} bytes")

    # Generate metadata
    write_json(os.path.join(out, "pack_table.json"), gen_pack_table())
    write_json(os.path.join(out, "active.json"), gen_active())
    write_json(os.path.join(pack_dir, "sequences_lut.json"), gen_sequences_lut())
    write_json(os.path.join(assign_dir, f"{FAKE_NODE_UUID}.json"),
               gen_assignment(lshow_hashes))

    # Export expected patterns for Probe comparison
    export_expected_patterns(out)

    print(f"\nTest pack: {out}")
    print(f"  Pack UUID: {PACK_UUID}")
    print(f"  Fake UUID: {FAKE_NODE_UUID}")
    print(f"  Channels: {CHANNELS} × {PIXELS_PER_CH} pixels")
    print(f"  Sequences: {len(SEQUENCES)}")


if __name__ == "__main__":
    main()
