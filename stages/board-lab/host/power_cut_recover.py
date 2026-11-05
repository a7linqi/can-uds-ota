"""Recover and gather evidence after the install-window power cut of
2026-11-05 (v19, cut ~3-4 s into the post-11-01 install window).

power_cut_upgrade.py crashed AFTER the cut was detected (restoring the serial
timeout on a dead handle raised), so its phase 4-6 never ran. The board was
left unpowered with metadata READY/INSTALLING and a half-erased internal app
slot. This script does exactly what those phases would have done:

    1. wait for the operator to restore power (port reappears)
    2. read the cold-boot banner raw -- the board must redo the install by
       itself and hand off to v19; nobody sends a single UDS request first
    3. one explicit DTR/RTS reset, banner again
    4. status DID over UDS
    5. SWD dump of the internal payload + the 96-byte ticket, byte-for-byte
       comparison with the build product

    python host/power_cut_recover.py --port COM9 \
        --out evidence/power-cut-install
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient  # noqa: E402

APP_BASE = 0x08010000
TICKET_ADDR = 0x080FFFA0
TICKET_SIZE = 0x60
PAYLOAD_SIZE = 44824
CLI = r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
T0 = time.monotonic()

CUT_FACTS = {
    "phase": "install (post-11-01 window)",
    "package": "app_v19.img2, 44920 bytes, payload 44824",
    "cut_point": "3-4 s into the install window, mid erase/program of the "
                 "internal 960 KiB app slot (beeps reached 3 s, then the "
                 "handle died)",
    "host_script_note": "power_cut_upgrade.py crashed after detecting the cut "
                        "(serial timeout restore on a dead handle); this "
                        "script performs its phase 4-6",
}


def mark(text):
    print("[%7.2f] %s" % (time.monotonic() - T0, text), flush=True)


def read_banner(ser, seconds):
    data = b""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(4096)
        if chunk:
            data += chunk
            # The gap between "selecting boot target" and "handing off" is
            # 1-2 s of signature verification with the console silent; 1 s
            # of silence used to cut the read exactly there and lose the
            # handoff line, which is the whole point of the capture.
            end = time.monotonic() + 3.0
    return data


def dtr_reset(ser):
    ser.dtr = True
    ser.rts = True
    time.sleep(0.15)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.2)


def swd_dump(addr, size, out_path):
    result = subprocess.run(
        [CLI, "-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
         "-u", hex(addr), hex(size), str(out_path)],
        capture_output=True, text=True, timeout=120, errors="replace")
    ok = "File downloaded" in (result.stdout or "") or result.returncode == 0
    return {"rc": result.returncode, "ok": ok,
            "tail": (result.stdout or "").strip().splitlines()[-3:]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reconnect-timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"label": "recovery after install-window power cut (v19)",
              "cut": CUT_FACTS, "timeline": [], "steps": []}

    def stamp(text):
        report["timeline"].append({"t": round(time.monotonic() - T0, 2),
                                   "event": text})
        mark(text)

    stamp("waiting for the board to come back (timeout %.0f s)"
          % args.reconnect_timeout)
    transport = None
    end = time.monotonic() + args.reconnect_timeout
    while time.monotonic() < end:
        try:
            transport = SerialCanTransport(args.port, args.baud)
            stamp("port reopened -- power is back")
            break
        except Exception:
            time.sleep(1.0)
    if transport is None:
        report["recovery"] = "port never came back within timeout"
        (out / "recovery.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 2

    # The open itself pulses NRST: read what the boot prints, no flushing.
    banner1 = read_banner(transport.ser, 15.0)
    stamp("banner after reopen: %d bytes" % len(banner1))
    (out / "recovery-banner-reopen.bin").write_bytes(banner1)

    dtr_reset(transport.ser)
    banner2 = read_banner(transport.ser, 15.0)
    stamp("banner after explicit reset: %d bytes" % len(banner2))
    (out / "recovery-banner-reset.bin").write_bytes(banner2)

    client = UdsClient(IsoTpClient(transport), lambda **kw: None,
                       total_timeout=60)
    status_after = None
    # The first attempt can race the post-reset handoff (loader busy, app
    # not yet on the bus). A timeout there is not a verdict -- retry.
    for attempt in range(5):
        try:
            answer = client.request(bytes([0x22, 0x02, 0x00]))
            p = bytes.fromhex(answer.hex())[3:]
            if len(p) >= 44:
                status_after = {
                    "state": p[2], "role": "application" if p[43] & 0x80 else "loader",
                    "active_version": int.from_bytes(p[4:8], "big"),
                    "active_size": int.from_bytes(p[16:20], "big"),
                    "pending_version": int.from_bytes(p[8:12], "big"),
                    "pending_size": int.from_bytes(p[20:24], "big"),
                    "seq": int.from_bytes(p[32:36], "big")}
                stamp("after recovery: %s" % status_after)
            break
        except Exception as error:
            stamp("status read attempt %d failed: %s" % (attempt + 1, error))
            time.sleep(2.0)
    try:
        transport.close()
    except Exception:
        pass

    # The dumps can race a boot in progress (first attempt on 2026-11-05 hit
    # "Data read failed" while the loader was mid-boot); give the board a
    # moment and retry each dump once.
    time.sleep(3.0)
    payload_dump = swd_dump(APP_BASE, PAYLOAD_SIZE, out / "internal-after.bin")
    if not payload_dump["ok"]:
        time.sleep(3.0)
        payload_dump = swd_dump(APP_BASE, PAYLOAD_SIZE, out / "internal-after.bin")
    ticket_dump = swd_dump(TICKET_ADDR, TICKET_SIZE, out / "ticket-after.bin")
    if not ticket_dump["ok"]:
        time.sleep(3.0)
        ticket_dump = swd_dump(TICKET_ADDR, TICKET_SIZE, out / "ticket-after.bin")
    app_bin = (CORE / "build-temp/stage-d/app/app.bin").read_bytes()
    verdict = {"payload_dump": payload_dump, "ticket_dump": ticket_dump}
    if payload_dump["ok"]:
        got = (out / "internal-after.bin").read_bytes()
        verdict["payload_identical_to_build"] = got == app_bin
        verdict["payload_sha256"] = hashlib.sha256(got).hexdigest()[:12]
    if ticket_dump["ok"]:
        ticket = (out / "ticket-after.bin").read_bytes()
        verdict["ticket_present"] = (len(ticket) == TICKET_SIZE
                                     and ticket[32:] != b"\x00" * 64)

    report["recovery"] = {
        "status_after": status_after,
        "banner_reopen_has_selecting": b"selecting boot target" in banner1,
        "banner_reopen_has_handoff": b"handing off to the app" in banner1,
        "banner_reset_has_handoff": b"handing off to the app" in banner2,
        "verdict": verdict,
    }
    recovered = (status_after is not None
                 and status_after["role"] == "application"
                 and status_after["active_version"] == 19
                 and verdict.get("payload_identical_to_build") is True
                 and verdict.get("ticket_present") is True)
    report["recovery"]["RECOVERED"] = recovered
    stamp("VERDICT: %s" % ("RECOVERED -- the board redid the interrupted "
                           "install by itself and now runs v19"
                           if recovered else "NOT the expected state"))

    (out / "recovery.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n--- banner after reopen (cold boot, self-heal) ---")
    print(banner1.decode("ascii", "replace"))
    print("\n--- banner after explicit reset ---")
    print(banner2.decode("ascii", "replace"))
    return 0 if recovered else 3


if __name__ == "__main__":
    sys.exit(main())
