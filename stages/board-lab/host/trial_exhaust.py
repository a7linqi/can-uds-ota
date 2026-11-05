"""Burn down the TRIAL boot counter and watch the rollback, on one connection.

The rollback path has only ever had host-model coverage since the image
ticket landed: install() now writes the proof into the last 96 bytes of the
app area, and create_backup() copies the live ticket into the backup
container so a restore replays a proof instead of minting one.  If either
half of that is wrong, a rollback ends with an app nobody can vouch for and
the loader parks in diagnostics forever -- which is exactly the failure that
looks fine in a simulator.

Resets go through 0x11 01 rather than closing the serial port.  Closing it
asserts NRST through the CH340 (one reset) and reopening asserts it again
(two per cycle), so a counter driven that way advances in steps of two and
the attempt numbers stop matching what the wire says.

    python host/trial_exhaust.py --out evidence/trial-ticket/exhaust
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from power_cycle_watch import RecordingSerial, banner_lines   # noqa: E402

CORE = Path(r"D:\Project\Can-learning\ota-core")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument("--boot-wait", type=float, default=4.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(CORE / "tools"))
    import serial                                     # noqa: E402
    from pc.transport import SerialCanTransport       # noqa: E402
    from pc.isotp_client import IsoTpClient           # noqa: E402
    from pc.uds_client import UdsClient               # noqa: E402

    t0 = time.monotonic()
    real = serial.Serial(args.port, 115200, timeout=0.01)
    real.dtr = False
    real.rts = False
    recorder = RecordingSerial(real)
    recorder.t0 = t0
    recorder.chunks.clear()
    transport = SerialCanTransport(args.port, serial_instance=recorder)
    client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=20)

    def status():
        try:
            record = client.status()
        except Exception as error:                    # noqa: BLE001
            return {"error": str(error)}
        return {key: record.get(key) for key in
                ("state_name", "role", "active_version", "active_size",
                 "metadata_sequence", "boot_attempts", "requests_dropped")}

    mark = len(recorder.chunks)
    timeline = []
    print("%-4s %-12s %-12s %-6s %-7s %s" %
          ("cyc", "state", "role", "ver", "attempt", "seq"), flush=True)
    for cycle in range(args.cycles + 1):
        before = status()
        timeline.append({"cycle": cycle, "phase": "status", **before})
        print("%-4d %-12s %-12s %-6s %-7s %s" %
              (cycle, before.get("state_name"), before.get("role"),
               before.get("active_version"), before.get("boot_attempts"),
               before.get("metadata_sequence")), flush=True)
        if cycle == args.cycles:
            break
        try:
            reply = client.request(b"\x11\x01")
            if reply[:2] != b"\x51\x01":
                print("     reset answered %s" % reply.hex(), flush=True)
        except Exception as error:                    # noqa: BLE001
            print("     reset failed: %s" % error, flush=True)
        time.sleep(args.boot_wait)

    final = status()
    transport.close()

    with recorder.lock:
        chunks = [c for i, c in enumerate(recorder.chunks) if i >= mark]
    banners = banner_lines(chunks)

    out = Path(args.out)
    if out.suffix.lower() != ".json":
        out = out.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cycles": args.cycles,
        "boot_wait_seconds": args.boot_wait,
        "timeline": timeline,
        "final": final,
        "banner": banners,
        "note": ("Every reset here is 0x11 01 on a connection that stayed "
                 "open, so the attempt counter advances by exactly one per "
                 "cycle."),
    }
    with open(out, "w", encoding="utf-8", newline="\r\n") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)

    print("")
    print("--- loader decisions (%d lines) ---" % len(banners), flush=True)
    for entry in banners:
        line = entry["line"]
        if "loader:" in line or "trial" in line.lower() or "restor" in line.lower() \
           or "attempt" in line.lower() or "backup" in line.lower():
            print("  +%06.2fs  %s" % (entry["t"], line), flush=True)
    print("-> %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
