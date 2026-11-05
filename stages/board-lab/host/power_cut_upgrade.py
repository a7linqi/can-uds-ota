"""Power cut DURING an upgrade, on real silicon.

The idle power cut on 2026-11-05 proved nothing was being written. This script
puts the board mid-transaction first, then waits for the operator to pull the
plug:

    10 02 (arm) -> 11 01 -> loader banner -> 10 02 -> 27 -> 22 02 00
    -> countdown -> 34 (erase, ~14 s) -> 36 frames (each one writes NOR)

Two cut points exist, selected with --cut-phase:

  download (default) -- pull anywhere from the first 0x34 byte to the last
    0x36 frame: external NOR is being erased or programmed, internal flash
    still holds the working app. Recovery must hand off to that old app.

  install -- pull INSIDE the ~8 s window that follows 11 01 after a complete
    download + 0x37. 0x37 only commits state READY; the real install (NOR
    backup, then erase of 960 KiB internal flash, program, ticket write)
    happens in boot_select() after the reset. The window opens when the
    banner prints "loader: selecting boot target" and closes ~8 s later.
    The script watches the raw console for that line, then beeps. Recovery
    must redo the install by itself and end up running the NEW image.

After the port comes back:
  1. reopen (the open itself pulses NRST), read the banner raw -- no
     reset_input_buffer(), that swallowed the cold-boot banner on 2026-11-05
  2. one explicit DTR/RTS reset for a guaranteed complete banner
  3. read the status DID
  4. dump internal flash + the 96-byte ticket over SWD and compare byte for
     byte with the build product

    python host/power_cut_upgrade.py --port COM9 --image <v18.img2> \
        --out evidence/power-cut-install --lead 60 --cut-phase install
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
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402
from uds_stage_d import calc_key, check_package, MAX_BLOCK  # noqa: E402

APP_BASE = 0x08010000
TICKET_ADDR = 0x080FFFA0
TICKET_SIZE = 0x60
CLI = r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
T0 = time.monotonic()


def mark(text):
    print("[%7.2f] %s" % (time.monotonic() - T0, text), flush=True)


def wall(t=None):
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def read_banner(ser, seconds):
    """Collect raw console bytes; do NOT flush first -- the open itself is a
    reset pulse and the banner starts immediately."""
    data = b""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(4096)
        if chunk:
            data += chunk
            end = time.monotonic() + 1.0  # keep reading while it talks
    return data


def dtr_reset(ser):
    """Pulse NRST through the CH340 lines, then release."""
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
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lead", type=float, default=60.0,
                    help="countdown seconds between unlock and the first 0x34")
    ap.add_argument("--cut-phase", choices=("download", "install"),
                    default="download",
                    help="download: pull while NOR is being written; "
                         "install: pull inside the post-11-01 install window")
    ap.add_argument("--reconnect-timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    package = Path(args.image).read_bytes()
    facts = check_package(package)
    if facts["problems"]:
        raise SystemExit("refusing to send a broken package: %s" % facts["problems"])
    expect_version = facts["version"] if args.cut_phase == "install" else None

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "label": "power cut during upgrade (phase: %s)" % args.cut_phase,
        "started_wall": wall(), "port": args.port,
        "package": {"path": str(Path(args.image).resolve()),
                    "bytes": len(package),
                    "sha256": hashlib.sha256(package).hexdigest(),
                    "header": facts},
        "timeline": [], "steps": [], "frames_sent": 0,
    }

    def stamp(text):
        report["timeline"].append({"t": round(time.monotonic() - T0, 2),
                                   "wall": wall(), "event": text})
        mark(text)

    transport = SerialCanTransport(args.port, args.baud)
    client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=300)

    def ask(text, payload, tolerate=False):
        started = time.monotonic()
        try:
            answer = client.request(payload)
            record = {"request": text, "response": answer.hex(" ").upper(),
                      "seconds": round(time.monotonic() - started, 3)}
        except NegativeResponse as error:
            record = {"request": text, "nrc": str(error),
                      "seconds": round(time.monotonic() - started, 3)}
        except Exception as error:
            record = {"request": text, "error": str(error),
                      "seconds": round(time.monotonic() - started, 3)}
            if not tolerate:
                raise
        report["steps"].append(record)
        mark("%-42s -> %s (%.2f s)" % (text, record.get("response") or
                                       record.get("nrc") or record.get("error"),
                                       record["seconds"]))
        return record

    status_before = None
    frames_planned = 0
    cut_detected_at = None
    try:
        # ---- phase 1: get into the loader, same port session ----
        stamp("arming handoff from the running app")
        ask("10 02 (arm handoff)", bytes([0x10, 0x02]))
        ask("11 01 (reset into loader)", bytes([0x11, 0x01]), tolerate=True)
        saved = transport.ser.timeout
        transport.ser.timeout = 0.2
        banner = b""
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            banner += transport.ser.read(4096)
        transport.ser.timeout = saved
        if b"stop requested" not in banner:
            raise SystemExit("handoff did not reach the loader")
        stamp("loader reached, banner %d bytes" % len(banner))

        ask("10 02", bytes([0x10, 0x02]))
        seed_record = ask("27 01", bytes([0x27, 0x01]))
        seed_hex = seed_record.get("response", "").split()
        if len(seed_hex) < 6:
            raise SystemExit("no seed: " + json.dumps(report["steps"][-1]))
        seed = int("".join(seed_hex[2:6]), 16)
        key = calc_key(seed)
        ask("27 02 %08X" % key, bytes([0x27, 0x02]) + key.to_bytes(4, "big"))
        before = ask("22 02 00 (before cut)", bytes([0x22, 0x02, 0x00]))
        p = bytes.fromhex(before.get("response", ""))[3:]
        if len(p) >= 44:
            status_before = {
                "state": p[2], "role": "application" if p[43] & 0x80 else "loader",
                "active_version": int.from_bytes(p[4:8], "big"),
                "active_size": int.from_bytes(p[16:20], "big"),
                "seq": int.from_bytes(p[32:36], "big")}
            stamp("before cut: %s" % status_before)

        # ---- phase 2: countdown so the operator can time the pull ----
        # S3 is 5 s (uds_poll() clears session AND unlock on expiry). A silent
        # countdown lets both expire, and 0x34 then answers NRC 0x22 exactly
        # like it did on the first attempt of 2026-11-05. Every request
        # refreshes last_request_ms, so 3E 00 every 2 s keeps them alive.
        stamp("COUNTDOWN %d s -- pull power any time after zero" % int(args.lead))
        end = time.monotonic() + args.lead
        next_keep = time.monotonic() + 2.0
        last_marked = None
        while time.monotonic() < end:
            now = time.monotonic()
            if now >= next_keep:
                try:
                    client.request(bytes([0x3E, 0x00]))
                except Exception as error:
                    stamp("keepalive failed during countdown: %s" % error)
                    raise SystemExit("board stopped answering before the erase "
                                     "even started -- no cut point")
                next_keep = now + 2.0
            remain = int(end - now)
            if remain <= 30 and remain % 10 == 0 and remain != last_marked:
                mark("PULL IN %d s (programming session kept alive)" % remain)
                last_marked = remain
            time.sleep(0.2)
        stamp("ZERO -- erase starts NOW, write window is open")

        # ---- phase 3: 34 then stream 36 until the port dies ----
        request = bytes([0x34, 0x00, 0x44]) + APP_BASE.to_bytes(4, "big") \
            + len(package).to_bytes(4, "big")
        loss = None
        try:
            download = ask("34 00 44 " + request[3:].hex(" ").upper(), request)
        except Exception as error:
            # The erase alone runs ~14 s. A cut inside it kills the port while
            # this one request is in flight: metadata is already DOWNLOADING,
            # NOR is being erased. That IS a valid cut point.
            loss = str(error)
            download = None
        if loss is None and "response" not in download:
            # An NRC here means nothing was written: no cut point, board fine.
            raise SystemExit("34 was refused (%s) -- no erase started, no cut "
                             "point; the board is untouched" % json.dumps(download))
        if loss is None:
            declared = download["response"].split()
            limit = int("".join(declared[2:4]), 16) if len(declared) >= 4 else 0
            step = MAX_BLOCK - 2
            if limit and step + 2 > limit:
                raise SystemExit("ECU declared %#x, frame would be %d" % (limit, step + 2))
            frames_planned = (len(package) + step - 1) // step

            counter, sent = 1, 0
            while sent < len(package):
                piece = package[sent:sent + step]
                try:
                    answer = client.request(bytes([0x36, counter & 0xFF]) + piece)
                    sent += len(piece)
                    counter = (counter + 1) & 0xFF
                    if sent % (step * 20) < step:
                        stamp("36 progress %d / %d bytes" % (sent, len(package)))
                except NegativeResponse as error:
                    # A refusal is protocol, not a power cut. Nothing after this
                    # point is evidence of anything.
                    raise SystemExit("36 refused after %d bytes, no cut happened: %s"
                                     % (sent, error))
                except Exception as error:
                    loss = str(error)
                    break
            report["frames_sent"] = (sent + step - 1) // step
        else:
            sent = 0
            report["frames_sent"] = 0

        cut_phase_actual = args.cut_phase
        if loss is None and args.cut_phase == "install":
            # ---- phase 3b: complete the download, then open the install
            # window with a reset. 0x37 only commits READY; the erase of the
            # internal app slot happens after 11 01 inside boot_select(). ----
            exit37 = ask("37", bytes([0x37]), tolerate=True)
            if "response" not in exit37:
                if "nrc" in exit37:
                    raise SystemExit("37 refused -- package not accepted, no install "
                                     "window, board untouched: %s" % json.dumps(exit37))
                # A transport failure here IS a power cut that landed while the
                # verify request was in flight -- not a refusal.
                loss = exit37.get("error", "transport lost during 37")
                report["cut_phase_actual"] = "verify-37"
            else:
                stamp("37 accepted -- package verified, state is READY")
            if loss is None:
                ask("11 01 (reset INTO the install window)", bytes([0x11, 0x01]),
                    tolerate=True)

                saved = transport.ser.timeout
                transport.ser.timeout = 0.5
                install_uart = b""
                window_open = None
                last_beep = None
                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline:
                    try:
                        chunk = transport.ser.read(4096)
                    except Exception as error:
                        loss = str(error)
                        break
                    if chunk:
                        install_uart += chunk
                        if window_open is None and b"selecting boot target" in install_uart:
                            window_open = time.monotonic() - T0
                            print("\n" + "*" * 62, flush=True)
                            print("*** PULL ALL POWER NOW -- INSTALL WINDOW IS OPEN ***",
                                  flush=True)
                            print("*" * 62, flush=True)
                    if window_open is not None:
                        elapsed = int(time.monotonic() - T0 - window_open)
                        if elapsed != last_beep:
                            print("*** %d s into the install window -- PULL NOW ***"
                                  % elapsed, flush=True)
                            last_beep = elapsed
                    time.sleep(0.1)
                # The port handle is dead after a cut; restoring the timeout
                # tries to reconfigure it and raises. That raise used to kill
                # the script AFTER a successful cut -- the evidence was lost.
                try:
                    transport.ser.timeout = saved
                except Exception:
                    pass
                (out / "install-window-uart.bin").write_bytes(install_uart)
                report["install_window"] = {
                    "opened_at": round(window_open, 2) if window_open is not None else None,
                    "uart_bytes": len(install_uart),
                    "selecting_seen": window_open is not None,
                    "handoff_seen_before_cut": b"handing off to the app" in install_uart,
                }
                if loss is None:
                    raise SystemExit("30 s with power on -- install completed and the "
                                     "app booted; the window was missed, no cut")
                cut_detected_at = time.monotonic() - T0
                if report["install_window"]["handoff_seen_before_cut"]:
                    cut_phase_actual = "after-install"
                    stamp("PORT DIED after the handoff line -- cut landed late, "
                          "the install itself had already finished")
                else:
                    stamp("PORT DIED inside the install window (%s)" % loss)
                report["cut_phase_actual"] = cut_phase_actual

        if loss is None:
            stamp("download finished WITHOUT a power cut (%d frames) -- "
                  "no 0x37 sent; rerun with a bigger window or pull earlier"
                  % report["frames_sent"])
        else:
            if cut_detected_at is None:
                cut_detected_at = time.monotonic() - T0
            stamp("PORT DIED after %d of %d bytes -- power cut detected (%s)"
                  % (sent, len(package), loss))
    finally:
        try:
            transport.close()
        except Exception:
            pass

    if cut_detected_at is None:
        (out / "power-cut.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["timeline"], indent=1))
        return 1  # no cut happened; nothing to recover from

    # ---- phase 4: wait for the operator to restore power ----
    stamp("waiting for the board to come back (timeout %.0f s)"
          % args.reconnect_timeout)
    transport2 = None
    end = time.monotonic() + args.reconnect_timeout
    while time.monotonic() < end:
        try:
            transport2 = SerialCanTransport(args.port, args.baud)
            stamp("port reopened")
            break
        except Exception:
            time.sleep(1.0)
    if transport2 is None:
        report["recovery"] = "port never came back within timeout"
        (out / "power-cut.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 2

    # The open itself pulses NRST: read what the boot prints, no flushing.
    banner1 = read_banner(transport2.ser, 8.0)
    stamp("banner after reopen: %d bytes" % len(banner1))
    (out / "recovery-banner-reopen.bin").write_bytes(banner1)

    dtr_reset(transport2.ser)
    banner2 = read_banner(transport2.ser, 10.0)
    stamp("banner after explicit reset: %d bytes" % len(banner2))
    (out / "recovery-banner-reset.bin").write_bytes(banner2)

    # ---- phase 5: what does the recovered board say it is? ----
    client2 = UdsClient(IsoTpClient(transport2), lambda **kw: None, total_timeout=60)
    status_after = None
    try:
        answer = client2.request(bytes([0x22, 0x02, 0x00]))
        p = bytes.fromhex(answer.hex()) [3:]
        if len(p) >= 44:
            status_after = {
                "state": p[2], "role": "application" if p[43] & 0x80 else "loader",
                "active_version": int.from_bytes(p[4:8], "big"),
                "active_size": int.from_bytes(p[16:20], "big"),
                "pending_version": int.from_bytes(p[8:12], "big"),
                "pending_size": int.from_bytes(p[20:24], "big"),
                "seq": int.from_bytes(p[32:36], "big")}
            stamp("after recovery: %s" % status_after)
    except Exception as error:
        stamp("status read failed: %s" % error)
    try:
        transport2.close()
    except Exception:
        pass

    # ---- phase 6: byte-level proof over SWD ----
    payload_size = facts["payload"]
    payload_dump = swd_dump(APP_BASE, payload_size, out / "internal-after.bin")
    ticket_dump = swd_dump(TICKET_ADDR, TICKET_SIZE, out / "ticket-after.bin")
    app_bin = (CORE / "build-temp/stage-d/app/app.bin").read_bytes()
    verdict = {"payload_dump": payload_dump, "ticket_dump": ticket_dump}
    if payload_dump["ok"]:
        got = (out / "internal-after.bin").read_bytes()
        verdict["payload_identical_to_build"] = got == app_bin
        verdict["payload_sha256"] = hashlib.sha256(got).hexdigest()[:12]
    if ticket_dump["ok"]:
        ticket = (out / "ticket-after.bin").read_bytes()
        verdict["ticket_present"] = len(ticket) == TICKET_SIZE and ticket[32:] != b"\x00" * 64

    report["recovery"] = {
        "cut_at": round(cut_detected_at, 2),
        "cut_phase": args.cut_phase,
        "cut_phase_actual": report.get("cut_phase_actual", args.cut_phase),
        "bytes_downloaded_before_cut": sent,
        "frames_sent_before_cut": report.get("frames_sent"),
        "status_before": status_before,
        "status_after": status_after,
        "banner_reopen_has_handoff": b"handing off to the app" in banner1,
        "banner_reset_has_handoff": b"handing off to the app" in banner2,
        "verdict": verdict,
    }
    expect = expect_version or (status_before or {}).get("active_version")
    recovered = (status_after is not None
                 and status_after["role"] == "application"
                 and status_after["active_version"] == expect
                 and verdict.get("payload_identical_to_build") is True
                 and verdict.get("ticket_present") is True)
    report["recovery"]["expected_active_version"] = expect
    report["recovery"]["RECOVERED"] = recovered
    if recovered:
        if args.cut_phase == "install":
            stamp("VERDICT: RECOVERED -- board redid the interrupted install "
                  "by itself and now runs v%d" % expect)
        else:
            stamp("VERDICT: RECOVERED -- board runs v%d after an interrupted "
                  "upgrade" % expect)
    else:
        stamp("VERDICT: NOT the expected state")

    (out / "power-cut.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n--- banner after explicit reset ---")
    print(banner2.decode("ascii", "replace"))
    return 0 if recovered else 3


if __name__ == "__main__":
    sys.exit(main())
