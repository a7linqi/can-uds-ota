"""Measure the hardware watchdog period on real silicon.

The wd_bite image starts the IWDG (prescaler 256, reload 4095) and never
reloads it, so the board resets itself. Every boot prints one banner line;
the gap between two of those lines is the measured timeout of this chip's
LSI -- not the datasheet's 32.768 s.

    python host/watchdog_period.py --port COM9 --seconds 150
"""
import argparse
import statistics
import sys
import time

import serial

MARKERS = ("wd_bite: watchdog started", "wd_bite: BSP init failed",
           "wd_bite: watchdog start FAILED")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--marker", default="wd_bite: watchdog started")
    args = ap.parse_args(argv)

    ser = serial.Serial(args.port, args.baud, timeout=1.0)
    # Opening the port raises DTR and RTS, and this board wires them to
    # BOOT0/RESET: without dropping both lines the MCU never leaves reset and
    # the log stays empty (measured the hard way on 2026-11-05).
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    started = time.monotonic()
    hits = []
    lines = 0
    try:
        while time.monotonic() - started < args.seconds:
            raw = ser.readline()
            if not raw:
                continue
            lines += 1
            text = raw.decode("ascii", "replace").strip()
            t = time.monotonic() - started
            if any(m in text for m in MARKERS):
                hits.append((round(t, 3), text))
                print("[%8.3f] %s" % (t, text), flush=True)
            elif "ATK Explorer" in text:
                print("[%8.3f] (banner) %s" % (t, text), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    print("\nlines=%d markers=%d" % (lines, len(hits)))
    if len(hits) >= 2:
        gaps = [round(hits[i + 1][0] - hits[i][0], 3)
                for i in range(len(hits) - 1)]
        print("periods between boots: %s" % gaps)
        print("median period: %.3f s  (nominal IWDG timeout 32.768 s)"
              % statistics.median(gaps))
        print("implied LSI: %.1f kHz" % (4096.0 * 256.0 / statistics.median(gaps) / 1000.0))
    else:
        print("not enough markers to compute a period")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
