"""Watch one real power cycle of the board without perturbing it.

power_probe.py samples once and exits, so it can only catch an outage someone
happened to time.  This keeps one serial handle open for the whole experiment
and timestamps three independent signals:

  * ``3E 00`` / ``7E 00`` about twice a second -- the MCU is executing our
    diagnostic loop.  Single frame, no side effects, no session change, and
    req/send is stateless, so a timeout does not poison the next request.
  * ST-Link target voltage every few seconds in ``mode=HOTPLUG`` -- the rail
    is up.  HOTPLUG does not assert NRST (verified: four connects, metadata
    sequence unchanged), which is why ``mode=UR`` from power_probe must not
    be used here.
  * everything else on the wire, decoded as text -- the loader banner.

The port is opened exactly once and never closed.  On this carrier closing
COM9 asserts NRST through the CH340, so a monitor that reconnected every
second would reset the board every second and measure its own noise (see
journal/entries/2026-11-05-port-close-resets-the-board.md).

If the board's USB link is downstream of its power switch the port can
disappear entirely; that is treated as evidence, not as an error, and the
script tries to reopen it.

    python host/power_cycle_watch.py --seconds 300 --out evidence/power/cycle-1
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CLI = r"D:/Keil/CubProgram/bin/STM32_Programmer_CLI.exe"
CORE = Path(r"D:\Project\Can-learning\ota-core")

TEXT_RUN = re.compile(rb"[ -~]{5,}")
LOOKS_LIKE_LOG = re.compile(r"[A-Za-z]{3,}")


class RecordingSerial:
    """Proxy that keeps the raw byte stream, so the banner is not lost.

    SerialCanTransport only hands back decoded CAN frames; the loader's
    printf output is not a frame and would be dropped by the decoder.  A
    transparent wrapper is cheaper than a second reader: one owner of the
    port, one decoder, and the text rides along in the same read() calls.
    """

    def __init__(self, real):
        self._real = real
        self.chunks = []          # [(wall_offset, bytes)]
        self.t0 = time.monotonic()
        self.lock = threading.Lock()

    def read(self, size=1):
        data = self._real.read(size)
        if data:
            with self.lock:
                self.chunks.append((time.monotonic() - self.t0, bytes(data)))
        return data

    def write(self, data):
        return self._real.write(data)

    def __getattr__(self, name):
        return getattr(self._real, name)


class VoltageSampler(threading.Thread):
    def __init__(self, every, t0):
        super().__init__(daemon=True)
        self.every = every
        self.t0 = t0
        self.samples = []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.wait(self.every):
            self.samples.append(self.sample())

    def sample(self):
        started = time.monotonic() - self.t0
        try:
            # 15 s, not 25: while the target is unpowered the CLI can spend a
            # long time retrying, and a sample stuck in that retry would miss
            # the outage it was meant to observe.
            run = subprocess.run([CLI, "-c", "port=SWD", "mode=HOTPLUG", "-q"],
                                 capture_output=True, text=True, timeout=15,
                                 errors="replace")
            out = (run.stdout or "") + (run.stderr or "")
        except subprocess.TimeoutExpired:
            return {"t": started, "voltage": None, "state": "timeout"}
        match = re.search(r"Voltage\s*:\s*([0-9.]+)V", out)
        if "No STM32 target" in out:
            return {"t": started, "voltage": None, "state": "no-target"}
        if not match:
            tail = (out.strip().splitlines() or ["?"])[-1]
            return {"t": started, "voltage": None, "state": "no-voltage-line",
                    "detail": tail}
        return {"t": started, "voltage": float(match.group(1)), "state": "ok"}


def banner_lines(chunks):
    """Pull printable runs out of the raw stream, carrying split runs over.

    The loader prints whole lines but the UART delivers them in 64-byte
    reads, so a line can straddle two chunks.  A run that touches the end of
    the available data is held back until the next chunk arrives.
    """
    lines = []
    carry, carry_at = b"", None
    for at, data in chunks:
        blob = carry + data
        start = at if carry_at is None else carry_at
        runs = list(TEXT_RUN.finditer(blob))
        if not runs:
            carry, carry_at = b"", None
            continue
        for index, run in enumerate(runs):
            text = run.group().decode("ascii", "replace").strip()
            if index == len(runs) - 1 and run.end() == len(blob):
                carry, carry_at = run.group(), at
                break
            if len(text) >= 5 and LOOKS_LIKE_LOG.search(text):
                lines.append({"t": round(at, 3), "line": text})
        else:
            carry, carry_at = b"", None
    if carry and len(carry) >= 5:
        lines.append({"t": round(carry_at or 0.0, 3),
                      "line": carry.decode("ascii", "replace").strip()})
    # Adjacent duplicate banners are common (the loader repeats on every
    # reset); keep them all, they carry timing information.
    return lines


def intervals(samples, key):
    """Contiguous [start, end] spans where key(sample) is False."""
    spans, open_at = [], None
    for sample in samples:
        down = not key(sample)
        if down and open_at is None:
            open_at = sample["t"]
        elif not down and open_at is not None:
            spans.append([round(open_at, 3), round(sample["t"], 3)])
            open_at = None
    if open_at is not None:
        spans.append([round(open_at, 3), None])
    return spans


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--swd-every", type=float, default=4.0)
    parser.add_argument("--poll-every", type=float, default=0.5)
    parser.add_argument("--status-every", type=float, default=5.0)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(CORE / "tools"))
    import serial                                     # noqa: E402
    from pc.transport import SerialCanTransport       # noqa: E402
    from pc.isotp_client import IsoTpClient           # noqa: E402
    from pc.uds_client import UdsClient               # noqa: E402

    t0 = time.monotonic()
    real = serial.Serial(args.port, 115200, timeout=0.01)
    # Opening asserted DTR/RTS: the board just reset.  Drop both so the next
    # reset is the one the operator performs, not ours.
    real.dtr = False
    real.rts = False
    recorder = RecordingSerial(real)
    recorder.t0 = t0
    recorder.chunks.clear()
    transport = SerialCanTransport(args.port, serial_instance=recorder)
    client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=1.5)

    sampler = VoltageSampler(args.swd_every, t0)
    sampler.start()

    def stamp(t):
        return "+%07.2fs" % t

    baseline = None
    samples = []
    statuses = []
    events = []
    port_losses = []
    last_alive = None
    next_status_at = 0.0
    deadline = t0 + args.seconds

    print("watch: port %s open at %s, %.0f s budget" %
          (args.port, stamp(0.0), args.seconds), flush=True)
    print("watch: baseline boot (opening the port reset the board)", flush=True)

    while time.monotonic() < deadline:
        now = time.monotonic() - t0
        try:
            reply = client.request(b"\x3E\x00")
            alive = reply[:2] == b"\x7E\x00"
            rtt = round((time.monotonic() - t0 - now) * 1000.0, 1)
        except Exception as error:                    # noqa: BLE001
            alive = False
            rtt = None
            message = str(error)
            if "serial" in message.lower() and "fail" in message.lower():
                port_losses.append({"t": round(now, 3), "error": message})
                events.append({"t": round(now, 3), "event": "port-lost",
                               "detail": message})
                print("!!! %s serial port lost: %s" % (stamp(now), message),
                      flush=True)
                try:
                    transport = SerialCanTransport(args.port)  # reopen
                    real = getattr(transport, "ser", None)
                    recorder = RecordingSerial(real)
                    recorder.t0 = t0
                    transport = SerialCanTransport(args.port, serial_instance=recorder)
                    client = UdsClient(IsoTpClient(transport),
                                       lambda **kw: None, total_timeout=1.5)
                    events.append({"t": round(now, 3), "event": "port-reopened"})
                    print("### %s serial port reopened" % stamp(now), flush=True)
                except Exception as retry:            # noqa: BLE001
                    events.append({"t": round(now, 3), "event": "reopen-failed",
                                   "detail": str(retry)})

        if alive and last_alive is False:
            events.append({"t": round(now, 3), "event": "mc-answering-again"})
            print("### %s MCU answering again" % stamp(now), flush=True)
        if not alive and last_alive is not False:
            events.append({"t": round(now, 3), "event": "mc-stopped-answering"})
            print("!!! %s MCU stopped answering" % stamp(now), flush=True)
        last_alive = alive
        samples.append({"t": round(now, 3), "alive": alive, "rtt_ms": rtt})

        if alive and now >= next_status_at:
            next_status_at = now + args.status_every
            try:
                status = client.status()
                record = {
                    "t": round(now, 3),
                    "state": status.get("state_name"),
                    "role": status.get("role"),
                    "active_version": status.get("active_version"),
                    "active_size": status.get("active_size"),
                    "metadata_sequence": status.get("metadata_sequence"),
                    "requests_dropped": status.get("requests_dropped"),
                }
                statuses.append(record)
                if baseline is None:
                    baseline = record
                    print("watch: baseline %s v%s %s seq %s" %
                          (record["state"], record["active_version"],
                           record["role"], record["metadata_sequence"]),
                          flush=True)
                elif (record["metadata_sequence"] != statuses[0]["metadata_sequence"]
                      or record["state"] != statuses[0]["state"]):
                    print("watch: %s state=%s v%s seq %s" %
                          (stamp(now), record["state"], record["active_version"],
                           record["metadata_sequence"]), flush=True)
            except Exception:                          # noqa: BLE001
                pass

        if int(now) % 20 == 0 and abs(now - round(now)) < args.poll_every / 2:
            print("watch: %s alive=%s %s" % (stamp(now), alive,
                                             "" if sampler.samples else ""),
                  flush=True)
        time.sleep(args.poll_every)

    sampler.stop.set()
    try:
        final = client.status()
    except Exception as error:                        # noqa: BLE001
        final = {"error": str(error)}
    transport.close()

    with recorder.lock:
        chunks = list(recorder.chunks)
    banners = banner_lines(chunks)

    swd_down = intervals(sampler.samples,
                         lambda s: (s.get("voltage") or 0.0) > 1.0)
    mcu_down = intervals(samples, lambda s: s["alive"])

    out = Path(args.out)
    if out.suffix.lower() != ".json":
        out = out.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": args.label or out.stem,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds_watched": round(time.monotonic() - t0, 1),
        "baseline": baseline,
        "final": {k: final.get(k) for k in
                  ("state_name", "role", "active_version", "active_size",
                   "metadata_sequence", "requests_dropped")} if "error" not in final
                 else final,
        "swd": {
            "samples": sampler.samples,
            "unpowered_spans": swd_down,
            "min_voltage": min((s["voltage"] for s in sampler.samples
                                if s.get("voltage") is not None), default=None),
        },
        "mcu": {
            "polls": len(samples),
            "answered": sum(1 for s in samples if s["alive"]),
            "silent_spans": mcu_down,
            "events": events,
            "port_losses": port_losses,
        },
        "status_changes": statuses,
        "banner": banners,
        "note": ("The serial port stayed open for the whole run, so no reset "
                 "was caused by the monitor. Opening it at t=0 did reset the "
                 "board, which is what the baseline captures."),
    }
    with open(out, "w", encoding="utf-8", newline="\r\n") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)

    print("")
    print("watch: %d polls, %d answered, %d banners" %
          (len(samples), payload["mcu"]["answered"], len(banners)), flush=True)
    print("watch: swd min=%.2fV unpowered=%s" %
          (payload["swd"]["min_voltage"] or -1.0, swd_down), flush=True)
    print("watch: mcu silent=%s" % (mcu_down,), flush=True)
    print("watch: -> %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
