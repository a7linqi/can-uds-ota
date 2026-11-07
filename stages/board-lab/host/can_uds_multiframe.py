"""ISO-TP multi-frame VIN read over the real CAN link, with flow control."""
import json
import sys
import time
from pathlib import Path

import can

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(r"D:\Project\Can\stages\board-lab\evidence\can\uds_multiframe_evidence.json")
events = []
bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
t0 = time.time()


def rec(direction, msg):
    events.append({
        "t_s": round(time.time() - t0, 3),
        "dir": direction,
        "id": f"0x{msg.arbitration_id:03X}",
        "data": msg.data.hex(" ").upper(),
    })


def send(data, label):
    msg = can.Message(arbitration_id=0x7E0, data=data, is_extended_id=False)
    bus.send(msg, timeout=0.3)
    rec("tx", msg)
    print(f"TX [{label}] 0x7E0 {bytes(data).hex(' ').upper()}", flush=True)


def drain(seconds):
    frames = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            msg = bus.recv(timeout=0.08)
        except Exception as exc:  # noqa: BLE001
            print("  recv:", type(exc).__name__, str(exc)[:90], flush=True)
            continue
        if msg is None or not msg.is_rx or msg.is_error_frame:
            continue
        rec("rx", msg)
        frames.append(msg)
        print(f"  RX 0x{msg.arbitration_id:03X} {msg.data.hex(' ').upper()}", flush=True)
    return frames


payload = bytearray()
first = None
consecutive = []
try:
    send([0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0], "ReadDataByIdentifier VIN 0xF190")
    ff_frames = drain(0.6)
    for m in ff_frames:
        if m.arbitration_id == 0x7E8 and (m.data[0] & 0xF0) == 0x10:
            first = m
            total = ((m.data[0] & 0x0F) << 8) | m.data[1]
            payload += m.data[2:8]
            print(f"  ISO-TP FirstFrame: total_payload={total} bytes", flush=True)
            break
    if first is not None:
        send([0x30, 0x00, 0x00, 0, 0, 0, 0, 0], "ISO-TP FlowControl BS=0 STmin=0")
        cf_frames = drain(1.2)
        for m in cf_frames:
            if m.arbitration_id == 0x7E8 and (m.data[0] & 0xF0) == 0x20:
                consecutive.append(m)
                payload += m.data[1:8]
    # A short, single-frame status DID read as a second independent check.
    send([0x03, 0x22, 0x02, 0x00, 0, 0, 0, 0], "ReadDataByIdentifier status 0x0200")
    status_frames = drain(0.8)
    vin_ok = first is not None and payload[0:3] == bytes([0x62, 0xF1, 0x90])
    result = {
        "test": "ISO-TP multi-frame VIN read over real CAN",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bitrate": 500000,
        "first_frame": first.data.hex(" ").upper() if first else None,
        "consecutive_frames": [m.data.hex(" ").upper() for m in consecutive],
        "assembled_payload": payload.hex(" ").upper(),
        "assembled_bytes": len(payload),
        "positive_62_f190": vin_ok,
        "status_did_frames": [m.data.hex(" ").upper() for m in status_frames],
        "status": "passed" if vin_ok else "incomplete",
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nassembled ({len(payload)} B): {payload.hex(' ').upper()}", flush=True)
    print(f"result: {result['status']}  CF={len(consecutive)}  evidence: {OUT}", flush=True)
finally:
    try:
        bus.shutdown()
    except Exception:  # noqa: BLE001
        pass
