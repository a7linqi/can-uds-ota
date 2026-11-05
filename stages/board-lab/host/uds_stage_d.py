"""Drive the whole update path once: download -> verify -> install -> jump.

Stages B and C proved the write path with deliberately illegal packages. This
script sends a REAL signed IMG2 and then asks the ECU to reset, so the loader
gets its first chance to install something and hand off to it:

    10 02 -> 27 -> 34 -> 36 x N -> 37 -> 31 01 00 01 -> 11 01

Everything up to 0x31 happens over one connection (S3 is 5 s, so a second
process would drop back to the default session and look like a gate failure).

After 0x11 the loader reboots, erases 960 KiB of internal flash, installs the
image, enters TRIAL and jumps. That takes ~20-30 s with no bus traffic, so the
serial line is read raw rather than as CAN frames: the ASCII banner is the only
observable during the install.

    python host/uds_stage_d.py --port COM9 --image <app.img2> \
        --out evidence/stage-d/20261105
"""
import argparse
import hashlib
import json
import re
import struct
import sys
import time
import zlib
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402

APP_BASE = 0x08010000


def _header_constant(header, name, pattern):
    text = (CORE / header).read_text(encoding="utf-8")
    match = re.search(pattern % name, text)
    if not match:
        raise SystemExit("cannot read %s from %s" % (name, header))
    return int(match.group(1), 0)


MAX_BLOCK = _header_constant("include/ota/uds.h", "UDS_MAX_DOWNLOAD_BLOCK",
                             r"#define\s+%s\s+(\d+)u?")


def calc_key(seed):
    """Mirror of uds_calc_key() in src/core/uds.c -- not a secret, just demo."""
    k = seed ^ 0xA5A5A5A5
    k = ((k << 7) | (k >> 25)) & 0xFFFFFFFF
    return (k + 0x9E3779B9) & 0xFFFFFFFF


def check_package(raw):
    magic, version, size, payload_crc, load, flags, _ = struct.unpack_from("<7I", raw, 0)
    header_crc = struct.unpack_from("<I", raw, 28)[0]
    problems = []
    if magic != 0x32474D49:
        problems.append("magic is not IMG2")
    if header_crc != zlib.crc32(raw[:28]):
        problems.append("header CRC mismatch")
    if payload_crc != zlib.crc32(raw[32:32 + size]):
        problems.append("payload CRC mismatch")
    if load != APP_BASE:
        problems.append("load address is not 0x%08X" % APP_BASE)
    if not flags & 1:
        problems.append("package is not signed")
    if len(raw) != 32 + size + (64 if flags & 1 else 0):
        problems.append("package length does not match header")
    return {"version": version, "payload": size, "flags": flags,
            "payload_crc": "%08X" % payload_crc, "problems": problems}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--image", required=True)
    ap.add_argument("--listen", type=float, default=70.0,
                    help="seconds to record the serial line after 0x11")
    ap.add_argument("--out", required=True)
    ap.add_argument("--handoff", action="store_true",
                    help="start in the application and ask it for the loader "
                         "inside this same port session")
    args = ap.parse_args(argv)

    package = Path(args.image).read_bytes()
    facts = check_package(package)
    if facts["problems"]:
        raise SystemExit("refusing to send a broken package: %s" % facts["problems"])
    print("package version=%d payload=%d total=%d crc=%s"
          % (facts["version"], facts["payload"], len(package), facts["payload_crc"]))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = []
    console_lines = []
    transport = SerialCanTransport(args.port, args.baud)
    console = b""
    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=300)

        def ask(text, payload_bytes, tolerate=False):
            started = time.monotonic()
            failed = False
            try:
                answer = client.request(payload_bytes)
                record = {"request": text, "response": answer.hex(" ").upper(),
                          "seconds": round(time.monotonic() - started, 3)}
            except NegativeResponse as error:
                record = {"request": text, "nrc": str(error),
                          "seconds": round(time.monotonic() - started, 3)}
            except Exception as error:  # a reset can eat the last response
                failed = True
                record = {"request": text, "error": str(error),
                          "seconds": round(time.monotonic() - started, 3)}
            steps.append(record)
            print("%-46s -> %s  (%.2f s)" % (text, record.get("response") or
                                             record.get("nrc") or record.get("error"),
                                             record["seconds"]), flush=True)
            if failed and not tolerate:
                raise SystemExit("transport failed at %s" % text)
            return record

        if args.handoff:
            # Only the application can arm the handoff, and the board normally
            # boots into it. Do it here, inside the same port session: closing
            # the port pulses NRST (the CH340's DTR is wired to it), so a second
            # process would reset the board straight back into the application.
            # Measured 2026-11-05 -- close the port with the flag armed and the
            # metadata sequence rises by two (write + consume) while nobody is
            # listening: the loader took the stop and stayed.
            ask("10 02 (arm handoff)", bytes([0x10, 0x02]))
            ask("11 01 (reset into loader)", bytes([0x11, 0x01]), tolerate=True)
            # Save the transport's read timeout: it is 0.01 s and every later
            # 0x36 pays it on each poll. Leaving 0.2 s here made the download
            # 7x slower (0.43-2.47 s per frame instead of 0.05-0.34 s).
            saved = transport.ser.timeout
            transport.ser.timeout = 0.2
            deadline = time.monotonic() + 6.0
            banner = b""
            while time.monotonic() < deadline:
                banner += transport.ser.read(4096)
            transport.ser.timeout = saved
            sys.stdout.reconfigure(errors="replace")
            print("--- loader after handoff (%d bytes) ---" % len(banner))
            print(banner.decode("ascii", "replace"), flush=True)
            if b"stop requested" not in banner:
                raise SystemExit("handoff did not reach the loader")

        ask("10 02", bytes([0x10, 0x02]))
        seed_record = ask("27 01", bytes([0x27, 0x01]))
        seed_hex = seed_record.get("response", "").split()
        if len(seed_hex) < 6:
            raise SystemExit("no seed: " + json.dumps(steps[-1]))
        seed = int("".join(seed_hex[2:6]), 16)
        key = calc_key(seed)
        ask("27 02 %08X" % key, bytes([0x27, 0x02]) + key.to_bytes(4, "big"))
        ask("22 02 00 (before)", bytes([0x22, 0x02, 0x00]))

        request = bytes([0x34, 0x00, 0x44]) + APP_BASE.to_bytes(4, "big") \
            + len(package).to_bytes(4, "big")
        download = ask("34 00 44 " + request[3:].hex(" ").upper(), request)
        declared = download.get("response", "").split()
        limit = int("".join(declared[2:4]), 16) if len(declared) >= 4 else 0
        step = MAX_BLOCK - 2
        if limit and step + 2 > limit:
            raise SystemExit("ECU declared %#x, frame would be %d" % (limit, step + 2))

        counter, sent = 1, 0
        while sent < len(package):
            piece = package[sent:sent + step]
            ask("36 %02X <%d bytes> @%d" % (counter & 0xFF, len(piece), sent),
                bytes([0x36, counter & 0xFF]) + piece)
            sent += len(piece)
            counter = (counter + 1) & 0xFF
        ask("37 (verify signature over %d bytes)" % len(package), bytes([0x37]))
        ask("31 01 00 01 (verify READY package)", bytes([0x31, 0x01, 0x00, 0x01]))
        ask("22 02 00 (after 0x37)", bytes([0x22, 0x02, 0x00]))

        # 0x11 answers and then resets inside the same poll iteration, so the
        # positive response may race the reboot. Both outcomes are recorded.
        ask("11 01 (reset)", bytes([0x11, 0x01]), tolerate=True)

        try:
            transport.ser.reset_input_buffer()
        except Exception:
            pass
        reset_at = time.monotonic()
        deadline = reset_at + args.listen
        partial = b""
        while time.monotonic() < deadline:
            chunk = transport.ser.read(256)
            if chunk:
                console += chunk
                partial += chunk
                while b"\n" in partial:
                    line, _, partial = partial.partition(b"\n")
                    text = line.decode("ascii", "replace").strip()
                    if text:
                        console_lines.append([round(time.monotonic() - reset_at, 2), text])
                deadline = time.monotonic() + args.listen   # keep reading while it talks
        if partial.strip():
            console_lines.append([round(time.monotonic() - reset_at, 2),
                                  partial.decode("ascii", "replace").strip()])
    finally:
        transport.close()

    (out / "reset-console.bin").write_bytes(console)
    text = console.decode("ascii", "replace")
    printable = "".join(c if 32 <= ord(c) < 127 or c in "\r\n" else "." for c in text)
    (out / "reset-console.txt").write_text(printable, encoding="utf-8")

    report = {
        "port": args.port, "baud": args.baud,
        "package": {"path": str(Path(args.image).resolve()),
                    "bytes": len(package),
                    "sha256": hashlib.sha256(package).hexdigest(),
                    "header": facts},
        "frame_data_bytes": step,
        "frames": (len(package) + step - 1) // step,
        "seed": "%08X" % seed, "key": "%08X" % key,
        "steps": steps,
        "console_bytes": len(console),
        "console_lines": console_lines,
    }
    (out / "stage-d.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n--- serial after 0x11 (%d bytes) ---" % len(console))
    print(printable)
    return 0


if __name__ == "__main__":
    sys.exit(main())
