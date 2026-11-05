"""Ask the running application to hand the next reset to the loader.

Until now the only way to start a second upgrade was to wipe the metadata so
the loader saw an empty part. The application had no diagnostic transport at
all, so there was no way to say "I want to be programmed" from user code --
which is exactly the entry point a real ECU has.

This script drives that entry point and nothing else:

    22 02 00   read the status DID while the application owns the port
    10 02      enter programming session -> the App persists boot_request
    11 01      reset; the loader must stop instead of jumping to the App

It then reads the raw serial line and requires the loader to say it honoured
the request. A positive 50 02 alone is not evidence: the flag has to survive
the reset, so the loader banner is the part that matters.

    python host/uds_app_handoff.py --port COM9 --out evidence/<dir>

Follow with host/uds_stage_d.py to perform the actual upgrade; that script also
starts with 10 02, which the loader accepts normally.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen", type=float, default=25.0,
                    help="seconds to record the serial line after 0x11")
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect", default="loader: stop requested by the application",
                    help="exact loader line that proves the request survived")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = []
    console = b""
    console_lines = []
    transport = SerialCanTransport(args.port, args.baud)
    verdict = {"reached_loader": False, "handshake_ok": False}

    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=120)

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
            except Exception as error:   # the reset can eat the last response
                failed = True
                record = {"request": text, "error": str(error),
                          "seconds": round(time.monotonic() - started, 3)}
            steps.append(record)
            print("%-30s -> %s  (%.2f s)" % (text, record.get("response") or
                                             record.get("nrc") or record.get("error"),
                                             record["seconds"]), flush=True)
            if failed and not tolerate:
                raise SystemExit("transport failed at %s" % text)
            return record

        # The App answers this DID because it owns the port right now; the
        # loader would answer it too, so the value alone does not prove which
        # image is running. flash bytes are reported by both, active_version
        # is what differs once the App has committed.
        ask("22 02 00 (who is running)", bytes([0x22, 0x02, 0x00]))

        session = ask("10 02 (enter programming)", bytes([0x10, 0x02]))
        if session.get("response", "").startswith("50 02"):
            verdict["handshake_ok"] = True
        else:
            raise SystemExit("application did not accept 10 02: %s" % json.dumps(session))

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
                deadline = time.monotonic() + args.listen
        if partial.strip():
            console_lines.append([round(time.monotonic() - reset_at, 2),
                                  partial.decode("ascii", "replace").strip()])
    finally:
        transport.close()

    (out / "handoff-console.bin").write_bytes(console)
    text = console.decode("ascii", "replace")
    printable = "".join(c if 32 <= ord(c) < 127 or c in "\r\n" else "." for c in text)
    (out / "handoff-console.txt").write_text(printable, encoding="utf-8")
    verdict["reached_loader"] = args.expect in text
    verdict["jumped_to_app"] = "handing off to the app" in text
    verdict["loader_stayed_silent"] = len(console.strip()) == 0

    report = {
        "port": args.port, "baud": args.baud,
        "expect": args.expect,
        "verdict": verdict,
        "steps": steps,
        "console_bytes": len(console),
        "console_sha256": hashlib.sha256(console).hexdigest(),
        "console_lines": console_lines,
    }
    (out / "handoff.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n--- serial after 0x11 (%d bytes) ---" % len(console))
    print(printable)
    print("verdict:", json.dumps(verdict))
    return 0 if (verdict["handshake_ok"] and verdict["reached_loader"]) else 1


if __name__ == "__main__":
    sys.exit(main())
