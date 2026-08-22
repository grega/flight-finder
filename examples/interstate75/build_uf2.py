#!/usr/bin/env python3
"""Build a single combined .uf2 that installs BOTH the Pimoroni firmware AND the
flight-display application in one drag-and-drop.

The Pimoroni "...with-filesystem" firmware for the Interstate 75 W (RP2350)
reserves the top 2 MB of the 4 MB flash for a LittleFS filesystem (base
0x10200000, the tail of flash). This script:

  1. Builds a LittleFS image (block_size=4096, prog_size=256 - matching the rp2
     port's mkfs params) containing the app modules and a default config.py.
  2. Converts that image to UF2 blocks addressed at the filesystem base, using
     the SAME UF2 family id as the stock firmware (read from it, not hardcoded).
  3. Emits ONE coherent UF2: firmware blocks followed by filesystem blocks, with
     blockNo/numBlocks renumbered across the whole file so the RP2350 bootrom
     writes everything before it reboots (plain concatenation can reboot early,
     after the firmware's numBlocks are satisfied, and skip the filesystem).

The result is a factory image: drop it onto the RP2350 (BOOTSEL) drive once and
the device comes up with the interpreter, Pimoroni libs, and all app files in
place. secrets.py is omitted by default, so first boot lands in the WiFi setup
hotspot (FlightDisplay-XXXX) - no USB file copying, no per-device editing.

Usage:
    ./build_uf2.py --firmware i75w_rp2350-v0.0.5-micropython-with-filesystem.uf2
    ./build_uf2.py -f fw.uf2 -o flightdisplay-v0.0.5.uf2      # explicit output
    ./build_uf2.py -f fw.uf2 --config _device/config.py       # bake a device config
    ./build_uf2.py -f fw.uf2 --include-secrets                # also bake ./secrets.py

Firmware download (Interstate 75 W / RP2350, tested version):
    https://github.com/pimoroni/interstate75/releases/tag/v0.0.5
    -> i75w_rp2350-v0.0.5-micropython-with-filesystem.uf2

Requires: pip install littlefs-python
"""

import argparse
import os
import struct
import sys

from push import ALL_CODE_FILES, HERE  # same module set push.py ships

try:
    from littlefs import LittleFS
except ImportError:
    sys.exit(
        "This tool needs littlefs-python.\n"
        "    pip install littlefs-python\n"
        "(used to build the on-flash LittleFS image the firmware mounts)."
    )

# --- Flash geometry for the Interstate 75 W (RP2350) --------------------------
# Confirmed on-device via os.statvfs('/') and rp2.Flash().ioctl: the filesystem
# is the top 2 MB of the 4 MB flash. The rp2 port formats it with progsize=256
# (ports/rp2/modules/_boot.py); block_size/count come from the flash bdev.
XIP_BASE = 0x10000000
FLASH_SIZE = 0x400000          # 4 MB
FS_SIZE = 0x200000             # 2 MB reserved for LittleFS (tail of flash)
FS_BLOCK_SIZE = 4096
FS_READ_SIZE = 32
FS_PROG_SIZE = 256
LFS_DISK_VERSION = 0x00020000  # lfs2.0 - readable by any 2.x littlefs

# --- UF2 block format ---------------------------------------------------------
UF2_MAGIC_START0 = 0x0A324655  # "UF2\n"
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID = 0x00002000
UF2_PAYLOAD = 256              # RP2 convention: 256 data bytes per 512-byte block
UF2_BLOCK_SIZE = 512


def fs_base_addr():
    """Absolute XIP address of the LittleFS region (tail of flash)."""
    return XIP_BASE + (FLASH_SIZE - FS_SIZE)


def collect_files(config_path, include_secrets, secrets_path):
    """Return {device_filename: bytes} to bake into the filesystem image."""
    files = {}
    for name in ALL_CODE_FILES:
        with open(os.path.join(HERE, name), "rb") as fh:
            files[name] = fh.read()
    with open(config_path, "rb") as fh:
        files["config.py"] = fh.read()
    if include_secrets:
        if not os.path.exists(secrets_path):
            sys.exit(f"--include-secrets given but {secrets_path} does not exist")
        with open(secrets_path, "rb") as fh:
            files["secrets.py"] = fh.read()
    return files


def build_littlefs_image(files):
    """Build the raw 2 MB LittleFS image (bytes) containing `files`."""
    fs = LittleFS(
        block_size=FS_BLOCK_SIZE,
        block_count=FS_SIZE // FS_BLOCK_SIZE,
        read_size=FS_READ_SIZE,
        prog_size=FS_PROG_SIZE,
        disk_version=LFS_DISK_VERSION,
        mount=False,
    )
    fs.format()
    fs.mount()
    for name, data in files.items():
        with fs.open(name, "wb") as fh:
            fh.write(data)
    used = fs.used_block_count * FS_BLOCK_SIZE
    image = bytes(fs.context.buffer)
    assert len(image) == FS_SIZE, f"image is {len(image)} bytes, expected {FS_SIZE}"
    return image, used


def parse_uf2(data):
    """Parse a .uf2 into a list of block dicts. Validates magics."""
    if len(data) % UF2_BLOCK_SIZE != 0:
        sys.exit("firmware .uf2 length is not a multiple of 512 - not a UF2 file")
    blocks = []
    for off in range(0, len(data), UF2_BLOCK_SIZE):
        blk = data[off:off + UF2_BLOCK_SIZE]
        m0, m1, flags, addr, size, _blkno, _numblk, family = struct.unpack(
            "<8I", blk[:32]
        )
        (end,) = struct.unpack("<I", blk[508:512])
        if m0 != UF2_MAGIC_START0 or m1 != UF2_MAGIC_START1 or end != UF2_MAGIC_END:
            sys.exit(f"firmware .uf2 block at 0x{off:x} has bad magic - not a UF2 file")
        blocks.append({
            "flags": flags,
            "addr": addr,
            "size": size,
            "family": family if flags & UF2_FLAG_FAMILY_ID else None,
            "payload": blk[32:32 + size],
        })
    return blocks


def firmware_family(blocks):
    """The single UF2 family id used by the firmware (what the bootrom accepts)."""
    families = {b["family"] for b in blocks if b["family"] is not None}
    if not families:
        sys.exit("firmware .uf2 has no family id - cannot target the RP2350 safely")
    if len(families) > 1:
        sys.exit(f"firmware .uf2 mixes families {sorted(hex(f) for f in families)}; "
                 "unhandled - build the filesystem UF2 separately")
    return families.pop()


def image_to_blocks(image, base_addr, family):
    """Turn the filesystem image into UF2 block dicts addressed at base_addr."""
    blocks = []
    for off in range(0, len(image), UF2_PAYLOAD):
        chunk = image[off:off + UF2_PAYLOAD]
        blocks.append({
            "flags": UF2_FLAG_FAMILY_ID,
            "addr": base_addr + off,
            "size": len(chunk),
            "family": family,
            "payload": chunk,
        })
    return blocks


def pack_uf2(blocks):
    """Serialise blocks to bytes, renumbering blockNo/numBlocks as one image."""
    total = len(blocks)
    out = bytearray()
    for i, b in enumerate(blocks):
        payload = b["payload"]
        data = payload + b"\x00" * (476 - len(payload))
        out += struct.pack(
            "<8I",
            UF2_MAGIC_START0,
            UF2_MAGIC_START1,
            b["flags"],
            b["addr"],
            b["size"],
            i,
            total,
            b["family"] or 0,
        )
        out += data
        out += struct.pack("<I", UF2_MAGIC_END)
    return bytes(out)


def verify_roundtrip(uf2_bytes, base_addr, expected_files):
    """Re-extract the filesystem from the built UF2 and mount it, proving the
    image is valid and holds exactly the files we baked in."""
    blocks = parse_uf2(uf2_bytes)
    image = bytearray(b"\xff" * FS_SIZE)
    fs_end = base_addr + FS_SIZE
    for b in blocks:
        if base_addr <= b["addr"] < fs_end:
            start = b["addr"] - base_addr
            image[start:start + b["size"]] = b["payload"]
    fs = LittleFS(
        block_size=FS_BLOCK_SIZE,
        block_count=FS_SIZE // FS_BLOCK_SIZE,
        read_size=FS_READ_SIZE,
        prog_size=FS_PROG_SIZE,
        disk_version=LFS_DISK_VERSION,
        mount=False,
    )
    fs.context.buffer[:] = image
    fs.mount()  # raises if the image is not a valid mountable LittleFS
    found = {}
    for name in fs.listdir("/"):
        found[name] = fs.stat(name).size
    missing = set(expected_files) - set(found)
    if missing:
        sys.exit(f"round-trip check failed: missing {sorted(missing)} in image")
    return found


def human(n):
    return f"{n:,} B ({n / 1024:.1f} KiB)"


def main():
    parser = argparse.ArgumentParser(
        description="Combine Pimoroni firmware + flight-display code into one .uf2",
    )
    parser.add_argument("-f", "--firmware", required=True,
                        help="stock '...with-filesystem' firmware .uf2 to bundle")
    parser.add_argument("-o", "--output",
                        help="output .uf2 (default: flightdisplay-<fw-stem>.uf2)")
    parser.add_argument("--config", default=os.path.join(HERE, "config.py"),
                        help="config.py to bake in (default: ./config.py)")
    parser.add_argument("--include-secrets", action="store_true",
                        help="also bake ./secrets.py (default: omit -> first boot "
                             "starts the WiFi setup hotspot)")
    parser.add_argument("--secrets", default=os.path.join(HERE, "secrets.py"),
                        help="secrets.py path for --include-secrets (default: ./secrets.py)")
    args = parser.parse_args()

    if not os.path.exists(args.firmware):
        sys.exit(f"firmware not found: {args.firmware}")
    output = args.output
    if not output:
        stem = os.path.basename(args.firmware)
        stem = stem[:-4] if stem.lower().endswith(".uf2") else stem
        output = os.path.join(HERE, f"flightdisplay-{stem}.uf2")

    files = collect_files(args.config, args.include_secrets, args.secrets)
    image, used = build_littlefs_image(files)

    with open(args.firmware, "rb") as fh:
        fw_blocks = parse_uf2(fh.read())
    family = firmware_family(fw_blocks)

    # The "...with-filesystem" firmware ships a pre-formatted empty LittleFS in
    # the filesystem region (base .. base+something). Keep only the code blocks
    # below the FS base and substitute our populated image for that region; a
    # firmware code block crossing into the FS region means the geometry is wrong.
    base = fs_base_addr()
    code_blocks = [b for b in fw_blocks if b["addr"] < base]
    shipped_fs = [b for b in fw_blocks if b["addr"] >= base]
    code_end = max(b["addr"] + b["size"] for b in code_blocks)
    if code_end > base:
        sys.exit(
            f"firmware code extends to 0x{code_end:08x}, past the filesystem base "
            f"0x{base:08x} - the 2 MB/2 MB geometry assumption is wrong for this "
            "firmware; do not flash the result."
        )

    fs_blocks = image_to_blocks(image, base, family)
    combined = pack_uf2(code_blocks + fs_blocks)

    found = verify_roundtrip(combined, base, files)

    with open(output, "wb") as fh:
        fh.write(combined)

    print(f"firmware      : {args.firmware}")
    print(f"  family id   : 0x{family:08x}  (reused for filesystem blocks)")
    print(f"  code extent : 0x{XIP_BASE:08x}..0x{code_end:08x}  ({len(code_blocks)} blocks)")
    print(f"  shipped fs  : dropped {len(shipped_fs)} empty-filesystem blocks (replaced)")
    print(f"filesystem    : LittleFS lfs2.0, base 0x{base:08x}, size {human(FS_SIZE)}")
    print(f"  used        : {human(used)}  ({FS_SIZE - used:,} B free for runtime writes)")
    print("  files baked :")
    for name in sorted(found):
        print(f"      {name:<20} {found[name]:>7,} B")
    print(f"combined uf2  : {output}")
    print(f"  blocks      : {len(code_blocks) + len(fs_blocks)}  "
          f"({os.path.getsize(output):,} B)")
    print("  round-trip  : filesystem re-mounted OK, all files present")
    if "secrets.py" not in found:
        print("\nsecrets.py omitted -> first boot starts the FlightDisplay-XXXX "
              "setup hotspot.")
    print("\nFlash: hold BOOTSEL, plug in USB, drag this .uf2 onto the RP2350 drive.")


if __name__ == "__main__":
    main()
