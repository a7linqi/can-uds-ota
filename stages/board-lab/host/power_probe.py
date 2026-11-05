"""Tell whether the target board is powered, from two independent angles.

SWD and UDS answer different questions and a power-cut test needs both:

  * ST-Link reports the target reference voltage it measures on the SWD
    connector.  This survives a dead MCU only if something else still feeds
    the rail, so it is a statement about the power rail.
  * The UDS status DID answers only if the MCU is running our firmware and
    the UART is up, so it is a statement about the MCU.

A board whose MCU is halted by a debugger still shows voltage, and a board
whose rail is back up but whose firmware crashed shows voltage with no UDS
answer.  Reading both is the only way to tell those apart.

The UDS read closes the serial port when it is done, and closing it resets
the board on this carrier (CH340 DTR/RTS is wired to NRST), so every run of
this script costs one reset.  That is acceptable for a power test and is
recorded in the output as a note.

    python host/power_probe.py --label "switch OFF" --out evidence/power/off
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

CLI = r"D:/Keil/CubProgram/bin/STM32_Programmer_CLI.exe"
CORE = Path(r"D:\Project\Can-learning\ota-core")


def swd():
    """Return (voltage, state, detail) as reported by ST-Link."""
    try:
        run = subprocess.run([CLI, "-c", "port=SWD", "mode=UR", "-q"],
                             capture_output=True, text=True, timeout=20,
                             errors="replace")
    except subprocess.TimeoutExpired:
        return None, "timeout", "no answer within 20 s"
    out = (run.stdout or "") + (run.stderr or "")
    match = re.search(r"Voltage\s*:\s*([0-9.]+)V", out)
    voltage = float(match.group(1)) if match else None
    if "No STM32 target" in out:
        return voltage, "no-target", "ST-Link reports no target"
    if voltage is None:
        return None, "no-voltage-line", out.strip().splitlines()[-1:] or ["?"]
    return voltage, "ok", ""


def uds(port):
    """Return the status record, or the reason it could not be read."""
    sys.path.insert(0, str(CORE / "tools"))
    from pc.transport import SerialCanTransport      # noqa: E402
    from pc.isotp_client import IsoTpClient          # noqa: E402
    from pc.uds_client import UdsClient              # noqa: E402

    try:
        transport = SerialCanTransport(port, 115200)
    except Exception as error:                        # noqa: BLE001
        return None, "port-unavailable: %s" % error
    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=20)
        return client.status(), None
    except Exception as error:                        # noqa: BLE001
        return None, str(error)
    finally:
        transport.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--label", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--skip-uds", action="store_true",
                        help="do not touch the serial port (keeps the reset count at zero)")
    args = parser.parse_args(argv)

    voltage, swd_state, swd_detail = swd()
    result = {
        "label": args.label,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "swd": {"voltage": voltage, "state": swd_state, "detail": swd_detail},
        "powered": bool(voltage is not None and voltage > 1.0),
        "reset_cost": "1 reset per UDS read (CH340 DTR/RTS is wired to NRST)",
    }

    if not args.skip_uds:
        status, error = uds(args.port)
        result["uds"] = {"reachable": status is not None, "error": error}
        if status:
            result["uds"].update({
                "active_version": status.get("active_version"),
                "metadata_sequence": status.get("metadata_sequence"),
                "state_name": status.get("state_name"),
                "role": status.get("role"),
            })

    out = Path(args.out)
    if out.suffix.lower() != ".json":
        out = out.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\r\n") as handle:
        json.dump(result, handle, indent=1, ensure_ascii=False)

    print("swd  voltage=%s state=%s %s" % (voltage, swd_state, swd_detail))
    if "uds" in result:
        print("uds  reachable=%s %s" % (result["uds"]["reachable"],
                                        result["uds"].get("error") or ""))
    print("powered=%s  -> %s" % (result["powered"], out))
    return 0 if result["powered"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
