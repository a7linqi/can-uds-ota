"""Read-only physical CAN acceptance for board-lab Phase 3 (group E).

Reuses the FROZEN ota-core tools/pc ISO-TP/UDS client. Only info()/status()
(and the read_did calls they make for F190/F195/F186/0200) are exercised —
service 0x22 only. No flashing, security access, programming session, routine
control, or reset is ever issued. Every raw classic-CAN frame is recorded with
monotonic time; every transmitted UDS request is audited to be SID 0x22.
"""
import argparse
import json
import sys
import time
from pathlib import Path

CORE_TOOLS = Path(r"D:\Project\Can\ota-core\tools")
sys.path.insert(0, str(CORE_TOOLS))

from pc.transport import SimTransport, PythonCanTransport, Frame, TransportError  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient  # noqa: E402

ALLOWED_DIDS = {0xF190, 0xF195, 0xF186, 0x0200}


class LoggingTransport:
    """Record raw classic-CAN frames (dir, monotonic time, id, data) around an inner transport."""

    def __init__(self, inner):
        self.inner = inner
        self.events = []

    def _rec(self, direction, frame):
        self.events.append({"dir": direction, "t_mono": self.inner.now(),
                            "id": frame.arbitration_id, "data": frame.data.hex(),
                            "extended": frame.extended})

    def send(self, frame, timeout=1):
        self._rec("TX", frame)
        return self.inner.send(frame, timeout=timeout)

    def recv(self, timeout):
        frame = self.inner.recv(timeout)
        if frame is not None:
            self._rec("RX", frame)
        return frame

    def now(self):
        return self.inner.now()

    def wait(self, seconds):
        return self.inner.wait(seconds)

    def close(self):
        return self.inner.close()


def audit_sid_22(events):
    """Extract the UDS SID from each transmitted frame; verify it is 0x22."""
    sids = []
    for e in events:
        if e["dir"] != "TX":
            continue
        data = bytes.fromhex(e["data"])
        if not data:
            continue
        pci = data[0] >> 4
        if pci == 0:        # single frame: byte1 = SID
            sids.append(data[1])
        elif pci == 1:      # first frame: byte2 = SID
            sids.append(data[2])
        # consecutive/flow-control frames carry no SID
    bad = [s for s in sids if s != 0x22]
    return sids, bad


def run(transport, out_dir, repeats):
    log = LoggingTransport(transport)
    link = IsoTpClient(log, tx_id=0x7E0, rx_id=0x7E8)
    client = UdsClient(link)
    results = []
    successes = failures = 0
    try:
        for i in range(repeats):
            try:
                results.append({"kind": "info", "index": i, "value": client.info()})
                successes += 1
            except Exception as e:  # noqa: BLE001
                results.append({"kind": "info", "index": i, "error": str(e)})
                failures += 1
        for i in range(repeats):
            try:
                results.append({"kind": "status", "index": i, "value": client.status()})
                successes += 1
            except Exception as e:  # noqa: BLE001
                results.append({"kind": "status", "index": i, "error": str(e)})
                failures += 1
    finally:
        transport.close()
    sids, bad = audit_sid_22(log.events)
    summary = {
        "successes": successes, "failures": failures, "repeats": repeats,
        "tx_sids_hex": ["%02X" % s for s in sids],
        "non_0x22_sids": ["%02X" % s for s in bad],
        "only_sid_22": not bad and bool(sids),
        "frame_count": len(log.events),
        "allowed_dids": sorted("%04X" % d for d in ALLOWED_DIDS),
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "events.json").write_text(json.dumps(log.events, indent=2) + "\n", encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if (failures == 0 and summary["only_sid_22"]) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="use the in-process C simulator transport")
    ap.add_argument("--interface", help="python-can interface (e.g. 'pcan', 'slcan', 'vector')")
    ap.add_argument("--channel", help="python-can channel (explicit; never auto-detected)")
    ap.add_argument("--bitrate", type=int, default=500000)
    ap.add_argument("--cc", default=r"D:\Project\Can\toolchain\tccbox-2025.10.27.data\purelib\tccbox\tcc_dist\tcc.exe")
    ap.add_argument("--out-dir", default=r"D:\Project\Can\stages\board-lab\evidence\can")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    if args.simulate:
        transport = SimTransport(compiler=args.cc, flash_kib=1024)
    elif args.interface and args.channel is not None:
        transport = PythonCanTransport(args.interface, args.channel, bitrate=args.bitrate)
    else:
        print("specify --simulate OR both --interface and --channel (explicit real backend only)")
        return 2
    return run(transport, args.out_dir, args.repeats)


if __name__ == "__main__":
    raise SystemExit(main())
