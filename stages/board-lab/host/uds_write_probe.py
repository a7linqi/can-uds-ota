"""Send one real UDS download through the product path and predict the chip.

The point of this probe is not "the ECU answered positively". It writes the
image the chip SHOULD contain next to the JSON, so the chip can be asked for
its own 16 MiB CRC32 by the restore firmware:

    nor_restore.py --image <expected.bin> --mode verify --hardware

A device CRC equal to that file's CRC is a byte-exact statement about all
16 MiB, so it pins three things at once: the erase really covered one slot, the
payload sits at the offset we think, and nothing outside the slot moved.

0x34 erases the WHOLE download slot before it accepts a byte
(src/core/uds.c: erase(DOWNLOAD_OFFSET, EXT_IMAGE_SLOT_SIZE)). EXT_IMAGE_SLOT_SIZE
is 1 MiB -- the 2 MiB figure people remember is new+old slots together. This is
read out of flash_layout.h rather than copied, because two earlier runs of this
script predicted the wrong CRC from a hand-copied constant.

The payload is deliberately not a valid package, so 0x37 must refuse it with
NRC 0x72 and no update state may advance to UPDATE_READY.

Stage B: 256 bytes, one 0x36 frame  -> --size 256 --chunk 256
Stage C: 4096 bytes, 0x36 at the advertised 510 data bytes per frame, which
         crosses the 256-byte NOR page boundary on every frame after the first.
         That is the case the BSP hook used to break.

Usage:
    python host/uds_write_probe.py --port COM9 --out evidence/uds-stage-b
    python host/uds_write_probe.py --port COM9 --out evidence/uds-stage-c \
        --name stage-c --size 4096 --chunk 510 --pattern seq
"""
import argparse
import hashlib
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


def _header_constant(header, name, pattern):
    """Read a constant out of a product header instead of copying it.

    Two earlier runs of this probe assumed a 2 MiB download slot and predicted
    the wrong chip CRC: 0x34 erases EXT_IMAGE_SLOT_SIZE, which is 1 MiB
    (flash_layout.h). A hand-copied constant produced a wrong expectation that
    looked exactly like a wrong write.
    """
    text = (CORE / header).read_text(encoding="utf-8")
    match = re.search(pattern % name, text)
    if not match:
        raise SystemExit("cannot read %s from %s" % (name, header))
    return int(match.group(1), 0)


SLOT = _header_constant("include/ota/flash_layout.h", "EXT_IMAGE_SLOT_SIZE",
                        r"#define\s+%s\s+(0x[0-9A-Fa-f]+)")
MAX_BLOCK = _header_constant("include/ota/uds.h", "UDS_MAX_DOWNLOAD_BLOCK",
                             r"#define\s+%s\s+(\d+)u?")


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
    ap.add_argument("--chunk", type=int, default=None,
                    help="data bytes per 0x36 frame (default: all in one frame)")
    ap.add_argument("--pattern", choices=("fill", "seq"), default="fill")
    ap.add_argument("--fill", default="A5")
    ap.add_argument("--name", default="stage-b")
    ap.add_argument("--out", default=str(LAB / "build-temp/stage-b"))
    args = ap.parse_args(argv)

    if args.chunk is not None and args.chunk + 2 > MAX_BLOCK:
        raise SystemExit("chunk %d exceeds UDS_MAX_DOWNLOAD_BLOCK %d" % (args.chunk, MAX_BLOCK))
    if args.pattern == "seq":
        payload = bytes((0x30 + i) & 0xFF for i in range(args.size))
    else:
        payload = bytes([int(args.fill, 16)]) * args.size
    step = args.chunk or len(payload)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
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
        download = ask("34 00 44 " + request[3:].hex(" ").upper(), request)
        # The ECU states its own receive limit in the positive response. If the
        # frame we are about to send is bigger than that, the probe is wrong and
        # must say so instead of blaming the firmware for the NRC that follows.
        declared = download.get("response", "").split()
        if len(declared) >= 4:
            limit = int("".join(declared[2:4]), 16)
            if step + 2 > limit:
                raise SystemExit("ECU declared maxNumberOfBlockLength=%#x, frame is %d"
                                 % (limit, step + 2))

        counter, sent = 1, 0
        while sent < len(payload):
            piece = payload[sent:sent + step]
            transfer = bytes([0x36, counter & 0xFF]) + piece
            ask("36 %02X <%d bytes> @%d" % (counter & 0xFF, len(piece), sent), transfer)
            sent += len(piece)
            counter = (counter + 1) & 0xFF
        ask("37", bytes([0x37]))

        image = bytearray(GOLDEN.read_bytes())
        image[0:SLOT] = b"\xFF" * SLOT
        image[0:len(payload)] = payload
        image = bytes(image)
        expected = out / ("expected-after-%s.bin" % args.name)
        expected.write_bytes(image)

        report = {
            "backend": args.backend, "port": args.port, "baud": args.baud,
            "stage": args.name,
            "payload_bytes": len(payload), "payload_pattern": args.pattern,
            "payload_fill": args.fill if args.pattern == "fill" else None,
            "frame_data_bytes": step, "frames": (len(payload) + step - 1) // step,
            "ecu_max_block_length": declared[2:4],
            "seed": "%08X" % seed, "key": "%08X" % key,
            "steps": steps,
            "status_before": before.get("response"),
            "expected_image": {"path": str(expected),
                               "crc32": "%08X" % (zlib.crc32(image) & 0xFFFFFFFF),
                               "sha256": hashlib.sha256(image).hexdigest(),
                               "note": "golden with NOR[0,%#x) erased and the payload at 0" % SLOT},
            "writes": {"nor_slot_bytes": SLOT, "payload_bytes": len(payload),
                       "eeprom": "metadata.state=UPDATE_DOWNLOADING committed by 0x34"},
        }
        (out / ("%s.json" % args.name)).write_text(json.dumps(report, indent=2) + "\n",
                                                   encoding="utf-8")
        print(json.dumps({"expected_crc32": report["expected_image"]["crc32"],
                          "expected_image": str(expected)}, indent=2))
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
