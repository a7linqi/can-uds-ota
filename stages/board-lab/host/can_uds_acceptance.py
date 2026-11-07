"""Read-only UDS acceptance on the real CAN link (no firmware probes).

A fresh channel is opened for each round so a bus-off in one round cannot
poison the next one. Every frame is recorded as evidence.
"""
import json
import sys
import time
from pathlib import Path

import can

sys.stdout.reconfigure(encoding="utf-8")

OUT = Path(r"D:\Project\Can\stages\board-lab\evidence\can\uds_readonly_acceptance.json")
events = []
t_start = time.time()
REQ_VIN = [0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0]
REQ_STATUS = [0x03, 0x22, 0x02, 0x00, 0, 0, 0, 0]


def record(direction, msg):
    events.append({
        "t_s": round(time.time() - t_start, 3),
        "dir": direction,
        "id": f"0x{msg.arbitration_id:03X}",
        "dlc": msg.dlc,
        "data": msg.data.hex(" ").upper(),
    })


def one_round(index, request, label):
    print(f"--- round {index} [{label}] ---", flush=True)
    bus = None
    frames = []
    try:
        bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        msg = can.Message(arbitration_id=0x7E0, data=request, is_extended_id=False)
        bus.send(msg, timeout=0.3)
        record("tx", msg)
        print("TX 0x7E0", bytes(request).hex(" ").upper(), flush=True)
        deadline = time.time() + 0.6
        while time.time() < deadline:
            try:
                rx = bus.recv(timeout=0.1)
            except Exception as exc:  # noqa: BLE001
                print("  recv:", type(exc).__name__, str(exc)[:90], flush=True)
                continue
            if rx is None or not rx.is_rx or rx.is_error_frame:
                continue
            record("rx", rx)
            frames.append(rx)
            print(f"  RX 0x{rx.arbitration_id:03X} {rx.data.hex(' ').upper()}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("  round error:", type(exc).__name__, str(exc)[:110], flush=True)
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
    return frames


rounds = []
for i in range(1, 4):
    frames = one_round(i, REQ_VIN, "VIN 0xF190")
    rounds.append({"round": i, "did": "0xF190", "rx": [f.data.hex(" ").upper() for f in frames]})
frames = one_round(4, REQ_STATUS, "status 0x0200")
rounds.append({"round": 4, "did": "0x0200", "rx": [f.data.hex(" ").upper() for f in frames]})

responses = [e for e in events if e["dir"] == "rx" and e["id"] == "0x7E8"]
positive = [e for e in responses if "62" in e["data"].split()]
result = {
    "test": "real CAN physical + ISO-TP + UDS 0x22 read-only acceptance",
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "adapter": "UTC-T01 / PCAN-USB (VID 0C72:000C), PCANBasic.dll 4.6.2.753",
    "channel": "PCAN_USBBUS1",
    "bitrate": 500000,
    "board": "ATK Explorer STM32F407ZGT6, P5 jumper on CAN side",
    "firmware": "explorer_ZG_diagnostics_READONLY.bin (original, no probe)",
    "request_ids": ["0x7E0"],
    "response_ids": ["0x7E8"],
    "rounds": rounds,
    "rx_7e8_frames": len(responses),
    "positive_uds_responses": len(positive),
    "status": "passed" if positive else "failed",
    "events": events,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"\nresult: {result['status']}  "
      f"0x7E8 frames={len(responses)} positive={len(positive)}", flush=True)
print("evidence:", OUT, flush=True)
