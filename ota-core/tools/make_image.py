"""Wrap an actual fixed-address App BIN in the core's IMG2 container.

With --sign, appends a 64-byte Ed25519 signature over (32-byte header +
payload) and sets IMAGE_FLAG_SIGNED in the header flags. The MCU verifies
this signature before installing (see src/core/signature.c).
"""
import argparse
from pathlib import Path
import struct
import zlib

APP = 0x08010000
MAGIC = 0x32474D49
IMAGE_FLAG_SIGNED = 0x00000001
SIGNATURE_SIZE = 64

# Demo key seed matching include/ota/pubkey.h. NOT a production secret.
DEMO_SEED = bytes(range(32))


def _seed_from_hex(value):
    raw = bytes.fromhex(value)
    if len(raw) != 32:
        raise ValueError("seed must be 32 bytes (64 hex characters)")
    return raw


def package(payload, version, flash_kib=1024, seed=None):
    if not 0 < version <= 0xFFFFFFFF:
        raise ValueError("version must be 1..0xFFFFFFFF")
    if flash_kib not in (512, 1024):
        raise ValueError("flash capacity must be 512 or 1024 KiB")
    if not 8 <= len(payload) <= flash_kib * 1024 - 0x10000:
        raise ValueError("payload does not fit the internal app region")
    stack, reset = struct.unpack_from("<II", payload)
    ram = 0x20000000 < stack <= 0x20020000 or 0x10000000 < stack <= 0x10010000
    pc = reset & ~1
    if not ram or stack & 7 or not reset & 1 or not APP + 8 <= pc < APP + len(payload):
        raise ValueError("invalid MSP/Thumb vector or app not linked at 0x08010000")

    flags = IMAGE_FLAG_SIGNED if seed is not None else 0
    body = struct.pack("<7I", MAGIC, version, len(payload),
                       zlib.crc32(payload), APP, flags, 0)
    header = body + struct.pack("<I", zlib.crc32(body))
    data = header + payload
    if seed is not None:
        from ed25519 import generate_keypair, sign
        public_key, private_key = generate_keypair(seed)
        data += sign(data, public_key, private_key)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="raw App BIN, vector table first")
    parser.add_argument("output", type=Path, help="IMG2 package; not a direct-flash BIN")
    parser.add_argument("--version", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--flash-kib", type=int, choices=(512, 1024), default=1024)
    parser.add_argument("--sign", action="store_true",
                        help="append an Ed25519 signature (demo seed unless --seed)")
    parser.add_argument("--seed", help="32-byte signing seed as 64 hex chars")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("use a distinct output path")
    seed = None
    if args.sign:
        seed = _seed_from_hex(args.seed) if args.seed else DEMO_SEED
    try:
        data = package(args.input.read_bytes(), args.version, args.flash_kib, seed)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.write_bytes(data)
    kind = "signed" if seed is not None else "unsigned"
    print(f"IMG2({kind}): version={args.version}, payload={len(data)-32-(SIGNATURE_SIZE if seed is not None else 0)}, package={len(data)} bytes")


if __name__ == "__main__":
    main()
