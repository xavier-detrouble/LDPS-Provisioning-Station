#!/usr/bin/env python3
"""
Generate LDPS Test Pack — deterministic .lshow files + pack metadata.

Creates test patterns for production WS2812 signal verification:
  Seq 0: Color Verify — R→G→B, 3s @20fps, all channels same
  Seq 1: Channel Independence — each channel unique color, 5s @20fps
  Seq 2: Frame Rate Stress — B/W alternating, 10s @50fps

Output: static/test_pack/ directory with all pack files.

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
PIXELS_PER_CH = 10
COLOR_MODE_RGB = 3

SEQUENCES = [
    {
        "seq_index": 0,
        "name": "Color Verify",
        "file_uuid": "aa000001-0000-4000-a000-000000000001",
        "sequence_uuid": "bb000001-0000-4000-a000-000000000001",
        "duration_ms": 3000,
        "fps": 20,
        "description": "R>G>B all channels",
    },
    {
        "seq_index": 1,
        "name": "Channel Independence",
        "file_uuid": "aa000002-0000-4000-a000-000000000002",
        "sequence_uuid": "bb000002-0000-4000-a000-000000000002",
        "duration_ms": 5000,
        "fps": 20,
        "description": "Per-channel colors",
    },
    {
        "seq_index": 2,
        "name": "Frame Rate Stress",
        "file_uuid": "aa000003-0000-4000-a000-000000000003",
        "sequence_uuid": "bb000003-0000-4000-a000-000000000003",
        "duration_ms": 10000,
        "fps": 50,
        "description": "B/W alternating 50fps",
    },
]

# ── .lshow Binary Builder ────────────────────────────────────

def uuid_to_bytes(uuid_str: str) -> bytes:
    """Convert UUID string to 16 bytes."""
    return uuid.UUID(uuid_str).bytes


def build_lshow(seq: dict, frame_generator) -> bytes:
    """Build a complete .lshow v1 binary file."""
    fps = seq["fps"]
    frame_period_ms = 1000 // fps
    total_frames = seq["duration_ms"] // frame_period_ms

    # Generate frame data
    frame_data = bytearray()
    for frame_idx in range(total_frames):
        frame_bytes = frame_generator(frame_idx, total_frames)
        frame_data.extend(frame_bytes)

    # CRC-32 of frame data
    crc32 = zlib.crc32(frame_data) & 0xFFFFFFFF

    # Build header (96 bytes)
    header = bytearray(96)
    header[0:4] = b"LDS1"
    header[4] = 0x01  # version
    header[5] = CHANNELS  # channel_count
    struct.pack_into(">H", header, 6, total_frames)
    struct.pack_into(">H", header, 8, frame_period_ms)
    header[10] = 0  # reserved
    header[11] = COLOR_MODE_RGB
    struct.pack_into(">I", header, 12, seq["duration_ms"])
    header[16:32] = uuid_to_bytes(seq["file_uuid"])
    header[32:48] = uuid_to_bytes(seq["sequence_uuid"])
    struct.pack_into(">I", header, 48, crc32)
    # reserved2 [52:56] = 0
    desc = seq["description"].encode("utf-8")[:31]
    header[56:56 + len(desc)] = desc
    # reserved3 [88:96] = 0

    # Channel table (8 channels × 4 bytes)
    channel_table = bytearray()
    for _ in range(CHANNELS):
        channel_table.extend(struct.pack(">H", PIXELS_PER_CH))
        channel_table.extend(b"\x00\x00")  # reserved

    return bytes(header) + bytes(channel_table) + bytes(frame_data)


# ── Frame Generators ─────────────────────────────────────────

def gen_color_verify(frame_idx: int, total_frames: int) -> bytes:
    """Seq 0: R(1s) → G(1s) → B(1s), all channels same."""
    third = total_frames // 3
    if frame_idx < third:
        r, g, b = 255, 0, 0  # Red
    elif frame_idx < 2 * third:
        r, g, b = 0, 255, 0  # Green
    else:
        r, g, b = 0, 0, 255  # Blue

    # All 8 channels × 10 pixels × 3 bytes (RGB)
    pixel = bytes([r, g, b])
    return pixel * PIXELS_PER_CH * CHANNELS


def gen_channel_independence(frame_idx: int, total_frames: int) -> bytes:
    """Seq 1: Each channel has a unique fixed color."""
    colors = [
        (255, 0, 0),    # CH0: Red
        (0, 255, 0),    # CH1: Green
        (0, 0, 255),    # CH2: Blue
        (255, 255, 255),  # CH3: White
        (255, 255, 0),  # CH4: Yellow
        (0, 255, 255),  # CH5: Cyan
        (255, 0, 255),  # CH6: Magenta
        (128, 128, 128),  # CH7: Gray
    ]

    frame = bytearray()
    for ch in range(CHANNELS):
        r, g, b = colors[ch]
        frame.extend(bytes([r, g, b]) * PIXELS_PER_CH)
    return bytes(frame)


def gen_framerate_stress(frame_idx: int, total_frames: int) -> bytes:
    """Seq 2: Alternating black/white every frame at 50fps."""
    if frame_idx % 2 == 0:
        pixel = bytes([255, 255, 255])  # White
    else:
        pixel = bytes([0, 0, 0])  # Black

    return pixel * PIXELS_PER_CH * CHANNELS


GENERATORS = [gen_color_verify, gen_channel_independence, gen_framerate_stress]


# ── Pack Metadata Generators ─────────────────────────────────

def gen_pack_table() -> dict:
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "packs": [
            {
                "index": 0,
                "uuid": PACK_UUID,
                "name": "LDPS Test Pack",
                "sequence_count": len(SEQUENCES),
                "sync": True,
                "synced_version": "2026-05-03T00:00:00Z",
            }
        ],
    }


def gen_sequences_lut() -> dict:
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "entries": [
            {
                "seq_index": s["seq_index"],
                "sequence_uuid": s["sequence_uuid"],
                "name": s["name"],
                "time_ms": s["duration_ms"],
            }
            for s in SEQUENCES
        ],
    }


def gen_assignment(lshow_files: dict) -> dict:
    """Generate assignment for fake UUID. lshow_files = {file_uuid: sha256_hex}."""
    return {
        "schema": 1,
        "version": "2026-05-03T00:00:00Z",
        "entries": [
            {
                "seq_index": s["seq_index"],
                "file_uuid": s["file_uuid"],
                "sequence_uuid": s["sequence_uuid"],
                "sha256": lshow_files[s["file_uuid"]],
            }
            for s in SEQUENCES
        ],
    }


def gen_active() -> dict:
    return {"uuid": PACK_UUID}


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate LDPS Test Pack")
    parser.add_argument("--output", default="static/test_pack",
                        help="Output directory")
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
        print(f"  {seq['name']}: {total_frames} frames @ {seq['fps']}fps, "
              f"{file_size} bytes, SHA-256: {sha[:16]}...")

    # Generate pack metadata
    write_json(os.path.join(out, "pack_table.json"), gen_pack_table())
    write_json(os.path.join(out, "active.json"), gen_active())
    write_json(os.path.join(pack_dir, "sequences_lut.json"), gen_sequences_lut())
    write_json(os.path.join(assign_dir, f"{FAKE_NODE_UUID}.json"),
               gen_assignment(lshow_hashes))

    print(f"\nTest pack generated at: {out}")
    print(f"  Pack UUID: {PACK_UUID}")
    print(f"  Fake Node UUID: {FAKE_NODE_UUID}")
    print(f"  Sequences: {len(SEQUENCES)}")
    print(f"  .lshow files: {len(lshow_hashes)}")


def write_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  Written: {os.path.basename(path)}")


if __name__ == "__main__":
    main()
