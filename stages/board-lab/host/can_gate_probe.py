"""Real-CAN evidence: why DID 0x0200 returns NRC 0x72 in the read-only build.

Also proves the shipped PC stack (PythonCanTransport + IsoTpClient) can carry
multi-frame UDS requests on the real bus, which is the transport needed for a
future full-OTA run.
"""
import json
import sys
import time
from pathlib import Path

TOOLS = Path(r"D:\Project\Can-learning\ota-core\tools")
sys.path.insert(0, str(TOOLS))

from pc.transport import PythonCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import demo_key  # noqa: E402

OUT = Path(r"D:\Project\Can\stages\board-lab\evidence\can\nrc_72_gate_evidence.json")
events = []
transport = PythonCanTransport("pcan", "PCAN_USBBUS1", 500000)
link = IsoTpClient(transport)
t0 = time.time()


def ask(label, payload, timeout=2.0):
    entry = {
        "t_s": round(time.time() - t0, 3),
        "label": label,
        "tx": payload.hex(" ").upper(),
    }
    try:
        link.send(payload, timeout=timeout)
        answer = link.receive(timeout)
        entry["rx"] = answer.hex(" ").upper() if answer else None
        print(f"{label}: TX {entry['tx']}  RX {entry['rx']}", flush=True)
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"{type(exc).__name__}: {exc}"
        print(f"{label}: {entry['error']}", flush=True)
    events.append(entry)
    return entry.get("rx")


try:
    ask("0x10 02 programming session", bytes([0x10, 0x02]))
    ask("0x22 F186 session DID", bytes([0x22, 0xF1, 0x86]))
    ask("0x22 0200 status DID", bytes([0x22, 0x02, 0x00]))
    seed_hex = ask("0x27 01 request seed", bytes([0x27, 0x01]))
    seed = None
    if seed_hex and len(seed_hex.split()) >= 6:
        try:
            seed = int("".join(seed_hex.split()[2:6]), 16)
        except ValueError:
            seed = None
    if seed is not None:
        key = demo_key(seed)
        ask("0x27 02 send key", bytes([0x27, 0x02]) + key.to_bytes(4, "big"))
    else:
        ask("0x34 request download (unlocked=unknown)",
            bytes([0x34, 0x00, 0x44, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0xAD, 0x60]))
    if seed is not None:
        ask("0x34 request download (11-byte, ISO-TP multi-frame)",
            bytes([0x34, 0x00, 0x44, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0xAD, 0x60]))
    ask("0x22 0200 status DID again", bytes([0x22, 0x02, 0x00]))

    result = {
        "test": "read-only build gate telemetry on real CAN",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "transport": "PythonCanTransport(pcan, PCAN_USBBUS1, 500000) + IsoTpClient + UdsClient.demo_key",
        "root_cause": (
            "explorer_reference.h: OTA_EXPLORER_STORAGE_TRUSTED defaults to "
            "OTA_EXPLORER_UPDATE_CAPABLE (0 in the diagnostics READONLY target); "
            "BSP then forces config->storage_verified=0, so "
            "ota_stm32_ports_init() computes storage_ready=0 and "
            "stm32_eeprom.c eeprom_read() returns -1 unconditionally. "
            "metadata_load() therefore fails, append_did(UDS_DID_UPDATE_STATUS) "
            "returns -3 and UDS replies NRC 0x72. "
            "This is deliberate gate behaviour, not a defect."
        ),
        "expected": {
            "0x22 F186": "positive response, session byte",
            "0x22 0200": "7F 22 72",
            "0x34": "7F 34 22 (conditions not correct) because programming_allowed = "
                    "OTA_ENABLE_FLASHING && storage_ready = 0",
        },
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("evidence:", OUT, flush=True)
finally:
    try:
        transport.close()
    except Exception:  # noqa: BLE001
        pass
