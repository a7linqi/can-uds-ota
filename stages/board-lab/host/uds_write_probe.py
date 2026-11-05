"""Send one real UDS download (0x34 + 0x36, then a deliberately invalid 0x37).

This is the first experiment that touches external NOR through the product
path, so it stays deliberately small and deliberately refuses to look like a
valid package:

  * payload = 256 bytes of 0xA5. The IMG2 header is therefore invalid, so
    0x37 must answer NRC 0x72 and no update state may advance.
  * 0x34 erases the WHOLE 2 MiB download slot before it accepts a byte
    (src/core/uds.c: erase(DOWNLOAD_OFFSET, EXT_IMAGE_SLOT_SIZE)). That erases
    the factory content of NOR[0, 2 MiB) -- the golden dump still has it and
    the restore drill can put it back, but the chip is no longer "golden"
    after this script runs, so it prints the image the chip SHOULD now match.

The expected image is written next to the JSON so the caller can sweep the
real chip against it:

    nor_restore.py --image <expected.bin> --mode verify --hardware

A device CRC that equals that file's CRC is a byte-exact statement about all
16 MiB: the slot is all 0xFF, our 256 bytes sit at offset 0, and every byte
outside the slot is unchanged.

Usage:
    python host/uds_write_probe.py --port COM9 --out build-temp/stage-b
"""
import argparse
import json
import re
import sys
import time
import zlib
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can-learning\ota-core")
GOLDEN = Path(r"D:\Project\Can\backups\nor-original\full\20261103_200408_779736\pass1\pass1.bin")
sys.path.insert(0, str(CORE / "tools"))

from pc.transport import SerialCanTransport, SimTransport  # noqa: E402
from pc.isotp_client import IsoTpClient, ProtocolError   # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse    # noqa: E402

APP_BASE = 0x08010000


def _layout_constant(name):
    """Read a constant out of the product layout header instead of copying it.

    The first two runs of this probe assumed a 2 MiB download slot and predicted
    the wrong chip CRC: 0x34 erases EXT_IMAGE_SLOT_SIZE, which is 1 MiB
    (flash_layout.h). A hand-copied constant produced a wrong expectation that
    looked exactly like a wrong write.
    """
    text = (CORE / "include/ota/flash_layout.h").read_text(encoding="utf-8")
    match = re.search(r"#define\s+%s\s+(0x[0-9A-Fa-f]+)" % name, text)
    if not match:
        raise SystemExit("cannot read %s from flash_layout.h" % name)
    return int(match.group(1), 16)


SLOT = _layout_constant("EXT_IMAGE_SLOT_SIZE")


def calc_key(seed):
    """Mirror of uds_calc_key() in src/core/uds.c -- not a secret, just demo."""
    k = seed ^ 0xA5A5A5A5
    k = ((k << 7) | (k >> 25)) & 0xFFFFFFFF
    return (k + 0x9E3779B9) & 0xFFFFFFFF


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=("sim", "uart"), default="uart")
    ap.add_argument("--cc", default=r"D:/Project/Can/toolchain/tccbox-2025.10.27.data/purelib/tccbox/tcc_dist/tcc.exe")
    ap.add_argument("--flash-kib", type=int, default=1024, choices=(512, 1024))
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--fill", default="A5")
    ap.add_argument("--out", default=str(LAB / "build-temp/stage-b"))
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = bytes([int(args.fill, 16)]) * args.size
    steps = []
    transport = (SimTransport(args.cc, args.flash_kib) if args.backend == "sim"
                 else SerialCanTransport(args.port, args.baud))
    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=180)

        def ask(text, payload_bytes):
            started = time.monotonic()
            try:
                answer = client.request(payload_bytes)
                record = {"request": text, "response": answer.hex(" ").upper(),
                          "seconds": round(time.monotonic() - started, 3)}
            except NegativeResponse as error:
                record = {"request": text, "nrc": str(error),
                          "seconds": round(time.monotonic() - started, 3)}
            steps.append(record)
            print("%-46s -> %s  (%.2f s)" % (text, record.get("response") or record["nrc"],
                                             record["seconds"]), flush=True)
            return record

        ask("10 02", bytes([0x10, 0x02]))
        seed_record = ask("27 01", bytes([0x27, 0x01]))
        seed_hex = seed_record.get("response", "").split()
        if len(seed_hex) < 6:
            raise SystemExit("no seed: " + json.dumps(steps[-1]))
        seed = int("".join(seed_hex[2:6]), 16)
        key = calc_key(seed)
        ask("27 02 %08X" % key, bytes([0x27, 0x02]) + key.to_bytes(4, "big"))

        before = ask("22 02 00", bytes([0x22, 0x02, 0x00]))
        request = bytes([0x34, 0x00, 0x44]) + APP_BASE.to_bytes(4, "big") \
            + args.size.to_bytes(4, "big")
        ask("34 00 44 " + request[3:].hex(" ").upper(), request)
        transfer = bytes([0x36, 0x01]) + payload
        ask("36 01 <%d bytes>" % len(payload), transfer)
        ask("37", bytes([0x37]))

        image = bytearray(GOLDEN.read_bytes())
        image[0:SLOT] = b"\xFF" * SLOT
        image[0:len(payload)] = payload
        image = bytes(image)
        expected = out / "expected-after-stage-b.bin"
        expected.write_bytes(image)

        report = {
            "backend": args.backend, "port": args.port, "baud": args.baud,
            "payload_bytes": len(payload), "payload_fill": args.fill,
            "seed": f"{seed:08X}", "key": f"{key:08X}",
            "steps": steps,
            "expected_image": {"path": str(expected),
                               "crc32": f"{zlib.crc32(image) & 0xFFFFFFFF:08X}",
                               "sha256": __import__("hashlib").sha256(image).hexdigest(),
                               "note": "golden with NOR[0,2MiB) erased and the payload at 0"},
            "writes": {"nor_slot_bytes": SLOT, "payload_bytes": len(payload),
                       "eeprom": "metadata.state=UPDATE_DOWNLOADING committed by 0x34"},
        }
        (out / "stage-b.json").write_text(json.dumps(report, indent=2) + "\n",
                                          encoding="utf-8")
        print(json.dumps({"expected_crc32": report["expected_image"]["crc32"],
                          "expected_image": str(expected)}, indent=2))
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
