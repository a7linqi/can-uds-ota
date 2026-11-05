"""Does the I2C bus clear actually survive resets landing inside EEPROM writes?

Baseline, 2026-11-05 afternoon, before i2c_bus_clear(): 6 resets, 2 boots
ended with "EEPROM read failed" and stayed broken -- the 24C02 was holding SDA
low from a reset that landed inside a write, and HAL_I2C_Init could not undo
it. After the fix the same board recovered, but nobody ever measured the
failure rate again. This script does.

Each cycle:

    open the port (that alone resets the board)
      -> read the banner raw and record its EEPROM verdict
      -> if the app is running, send 10 02, which makes the app write
         boot_request to the EEPROM; if the loader is running, send 11 01
      -> close the port after a small, swept delay, so the reset lands
         somewhere inside (or just around) that write
    repeat

A single "EEPROM read failed" after N cycles means the clear did not hold.
A clean run is the statistic the fix never had.

    python host/eeprom_write_reset_stress.py --port COM9 --cycles 60
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(r"D:\Project\Can-learning\ota-core\tools")))
from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402


def log(text):
    print(text, flush=True)


def read_banner(ser, max_seconds=3.0, quiet=0.5):
    data = b""
    end = time.monotonic() + max_seconds
    last = time.monotonic()
    while time.monotonic() < end:
        chunk = ser.read(4096)
        now = time.monotonic()
        if chunk:
            data += chunk
            last = now
        elif data and now - last > quiet:
            break
    return data


def parse(text):
    verdict = "unknown"
    if "EEPROM read failed" in text:
        verdict = "failed"
    elif "EEPROM 256-byte read complete" in text:
        verdict = "ok"
    crc = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("eeprom_crc32="):
            crc = line.split("=", 1)[1]
    role = "loader" if ("staying in the loader" in text or "stop requested" in text) else \
           ("app" if "app at 08010000" in text else
            ("app" if "handing off to the app" in text else "loader"))
    return verdict, crc, role


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--cycles", type=int, default=60)
    ap.add_argument("--delays", default="0,4,8,12,16,20,24,28,32,40,48,56,64",
                    help="millisecond delays between the write request and the reset")
    ap.add_argument("--out", default="evidence/eeprom-reset-stress")
    args = ap.parse_args(argv)

    delays = [int(x) for x in args.delays.split(",")]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"label": "reset-inside-EEPROM-write stress, post i2c_bus_clear",
              "port": args.port, "cycles": args.cycles,
              "delay_sweep_ms": delays, "records": []}

    for i in range(args.cycles):
        delay = delays[i % len(delays)] / 1000.0
        try:
            ser = serial.Serial(args.port, args.baud, timeout=0.05)
        except Exception as error:
            log("cycle %02d: port open failed (%s) -- retrying once" % (i + 1, error))
            time.sleep(0.5)
            ser = serial.Serial(args.port, args.baud, timeout=0.05)
        # Opening asserts DTR/RTS, which this board wires to NRST/BOOT0.
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass
        banner = read_banner(ser)
        text = banner.decode("ascii", "replace")
        verdict, crc, role = parse(text)

        action = None
        link = IsoTpClient(SerialCanTransport(args.port, args.baud, serial_instance=ser))
        if role == "app":
            action = "10 02"
            link.send(bytes([0x10, 0x02]))
        else:
            action = "11 01"
            link.send(bytes([0x11, 0x01]))
        ser.flush()
        time.sleep(delay)
        try:
            ser.close()   # dropping DTR/RTS is the reset
        except Exception:
            pass

        record = {"cycle": i + 1, "role": role, "action": action,
                  "reset_delay_ms": round(delay * 1000), "eeprom": verdict,
                  "eeprom_crc32": crc}
        report["records"].append(record)
        log("cycle %02d  role=%-6s action=%-5s delay=%2d ms  EEPROM=%-7s crc=%s"
            % (i + 1, role, action, round(delay * 1000), verdict, crc))
        time.sleep(0.2)

    verdicts = [r["eeprom"] for r in report["records"]]
    ok, failed = verdicts.count("ok"), verdicts.count("failed")
    crcs = [r["eeprom_crc32"] for r in report["records"] if r["eeprom_crc32"]]
    # A cycle that opens in the loader means the previous cycle's 10 02 got its
    # request all the way into the EEPROM before the reset. A cycle that opens
    # in the app means the reset landed before or during that write -- which is
    # exactly the window this test is trying to hit.
    landed = sum(1 for r in report["records"][1:] if r["role"] == "loader")
    report["summary"] = {
        "cycles": len(verdicts), "eeprom_ok": ok, "eeprom_failed": failed,
        "unknown": len(verdicts) - ok - failed,
        "writes_that_completed_before_reset": landed,
        "writes_cut_or_never_started": len(verdicts) - 1 - landed,
        "distinct_crc32": sorted(set(crcs)),
        "baseline_before_fix": "2 of 6 resets failed and stayed broken",
    }
    (out / "stress.json").write_text(json.dumps(report, indent=2) + "\n",
                                     encoding="utf-8")
    log("\nSUMMARY: %d cycles, EEPROM ok=%d failed=%d unknown=%d, crc set=%s"
        % (len(verdicts), ok, failed, len(verdicts) - ok - failed,
           sorted(set(crcs))))
    return 0 if failed == 0 and ok > 0 else 3


if __name__ == "__main__":
    sys.exit(main())
