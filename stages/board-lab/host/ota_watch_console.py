"""Drive one OTA while watching the console at the same time.

uds_stage_d.py only reads the console after the reset, which is fine for a
successful run and useless for a suspicious one: if the loader parks or the
watchdog bites mid-download, the message that says so is printed while the
script is busy sending 0x36 frames.

This driver drains the same serial handle between frames and timestamps
every line, so a watchdog reset or "background service failed" cannot hide.

    python host/ota_watch_console.py --port COM9 --image <pkg> --out <dir>
"""
import argparse
import json
import sys
import time
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402
from uds_stage_d import calc_key, check_package, MAX_BLOCK  # noqa: E402

APP_BASE = 0x08010000
T0 = time.monotonic()


def mark(text):
    print("[%7.2f] %s" % (time.monotonic() - T0, text), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    package = Path(args.image).read_bytes()
    facts = check_package(package)
    if facts["problems"]:
        raise SystemExit("broken package: %s" % facts["problems"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"package": args.image, "bytes": len(package),
              "header": facts, "console": [], "frames": [], "steps": []}

    transport = SerialCanTransport(args.port, args.baud)
    ser = transport.ser
    client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=30)

    def drain(tag):
        # non-blocking: the transport sets a 0.01 s timeout
        data = b""
        while True:
            chunk = ser.read(4096)
            if not chunk:
                break
            data += chunk
        if data:
            for line in data.decode("ascii", "replace").splitlines():
                line = line.strip()
                if line:
                    report["console"].append({"t": round(time.monotonic() - T0, 2),
                                              "tag": tag, "line": line})
                    mark("console[%s]: %s" % (tag, line))
        return data

    def ask(text, payload):
        started = time.monotonic()
        try:
            answer = client.request(payload)
            rec = {"request": text, "response": answer.hex(" ").upper(),
                   "seconds": round(time.monotonic() - started, 3)}
        except NegativeResponse as error:
            rec = {"request": text, "nrc": str(error),
                   "seconds": round(time.monotonic() - started, 3)}
        report["steps"].append(rec)
        mark("%-30s -> %s" % (text, rec.get("response") or rec.get("nrc")))
        return rec

    # handoff
    ask("10 02", bytes([0x10, 0x02]))
    ask("11 01", bytes([0x11, 0x01]))
    time.sleep(0.8)
    drain("handoff")
    ask("10 02 (loader)", bytes([0x10, 0x02]))
    seed = ask("27 01", bytes([0x27, 0x01]))
    try:
        hexes = seed["response"].split()[2:6]
        key = calc_key(int("".join(hexes), 16))
    except Exception:
        raise SystemExit("no seed: %s" % json.dumps(seed))
    ask("27 02", bytes([0x27, 0x02]) + key.to_bytes(4, "big"))

    # download
    request = bytes([0x34, 0x00, 0x44]) + APP_BASE.to_bytes(4, "big") \
        + len(package).to_bytes(4, "big")
    ask("34", request)
    step = MAX_BLOCK - 2
    counter, sent = 1, 0
    while sent < len(package):
        piece = package[sent:sent + step]
        started = time.monotonic()
        try:
            answer = client.request(bytes([0x36, counter & 0xFF]) + piece)
            rec = {"frame": counter, "response": answer.hex(" "), "ok": True,
                   "seconds": round(time.monotonic() - started, 3)}
        except NegativeResponse as error:
            rec = {"frame": counter, "nrc": str(error), "ok": False,
                   "seconds": round(time.monotonic() - started, 3)}
        report["frames"].append(rec)
        if rec["ok"]:
            sent += len(piece)
            counter = (counter + 1) & 0xFF
        else:
            mark("frame %d -> %s" % (rec["frame"], rec["nrc"]))
            drain("frame-%d" % rec["frame"])
            if len([f for f in report["frames"] if not f["ok"]]) >= 3:
                break
        drain("tick")

    (out / "watch.json").write_text(json.dumps(report, indent=2) + "\n",
                                    encoding="utf-8")
    ok = len([f for f in report["frames"] if f["ok"]])
    bad = len([f for f in report["frames"] if not f["ok"]])
    mark("frames ok=%d bad=%d  console lines=%d" % (ok, bad, len(report["console"])))
    try:
        transport.close()
    except Exception:
        pass
    return 0 if bad == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
