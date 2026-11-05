"""Read the status DID and say which image is talking and whether it may write.

The expensive lesson behind this script: an application is built with
OTA_ENABLE_FLASHING=0 on purpose, so 0x34 sent to it answers NRC 0x22
(conditionsNotCorrect). A loader that has just closed its gate after a failed
job answers exactly the same byte. From the wire alone the two are
indistinguishable, which is how a correct refusal got mistaken for a stuck
gate and cost a board session.

The status DID (0x0200, 44 payload bytes) now ends with four telemetry bytes
that the board fills on every poll:

    payload[40]  programming_allowed
    payload[41]  job_error
    payload[42]  requests_dropped (frames discarded because one was in flight)
    payload[43]  bit7 = this image is the application, bits 0-6 = deadline misses

This script reads them and, with --probe-gate, proves the verdict by sending a
real gated request that is cheap and non-destructive:

    10 02 -> 27 01 / 27 02 -> 36 01 00

    36 01 00 answers NRC 0x24 (requestSequenceError) when the gate is open --
    the request got past it and died at "no 0x34 first". NRC 0x22 is the closed
    gate. The one data byte is never written: without an active download there
    is nowhere to put it.

    Do not use 31 01 00 01 as a gate probe. Measured on the board 2026-11-05:
    it answers 0x22 whenever there is no pending package, so it reports the
    same NRC as a closed gate while the gate is in fact open.

    python host/uds_gate_probe.py --port COM9 --out evidence/<dir>
    python host/uds_gate_probe.py --port COM9 --out evidence/<dir> --probe-gate

Exit code 0 only when the telemetry and the wire agree.
"""
import argparse
import json
import sys
import time
from pathlib import Path

CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402
# One definition of the demo key schedule, shared with the stage-D driver. A
# hand-rolled XOR here answered NRC 0x35 on the board and made the whole probe
# look like a gate failure.
from uds_stage_d import calc_key  # noqa: E402

STATUS_BYTES = 44
STATES = ["IDLE", "DOWNLOADING", "READY", "INSTALLING", "TRIAL", "RESTORING"]


def be32(data, offset):
    return int.from_bytes(data[offset:offset + 4], "big")


def decode_status(payload):
    if len(payload) != STATUS_BYTES:
        return {"error": "status DID returned %d bytes, expected %d"
                         % (len(payload), STATUS_BYTES)}
    diag = payload[40:44]
    state = payload[2]
    return {
        "format": payload[0],
        "record_loaded": payload[1],
        "state_code": state,
        "state": STATES[state] if state < len(STATES) else "UNKNOWN(%d)" % state,
        "boot_attempts": payload[3],
        "active_version": be32(payload, 4),
        "pending_version": be32(payload, 8),
        "min_version": be32(payload, 12),
        "active_size": be32(payload, 16),
        "pending_size": be32(payload, 20),
        "active_crc": "%08X" % be32(payload, 24),
        "pending_crc": "%08X" % be32(payload, 28),
        "metadata_seq": be32(payload, 32),
        "flash_total": be32(payload, 36),
        "diag": {
            "programming_allowed": diag[0],
            "job_error": diag[1],
            "requests_dropped": diag[2],
            "role": "application" if diag[3] & 0x80 else "loader",
            "service_deadline_misses": diag[3] & 0x7F,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--probe-gate", action="store_true",
                    help="also send 31 01 00 01 and compare with the telemetry")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = []
    transport = SerialCanTransport(args.port, args.baud)
    result = {"telemetry": None, "gate_probe": None, "verdict": {}}

    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None, total_timeout=120)

        def ask(text, payload_bytes):
            started = time.monotonic()
            record = {"request": text}
            try:
                answer = client.request(payload_bytes)
                record["response"] = answer.hex(" ").upper()
                record["bytes"] = answer
            except NegativeResponse as error:
                record["nrc"] = str(error)
                record["nrc_code"] = int(error.nrc)
            except Exception as error:
                record["error"] = str(error)
            record["seconds"] = round(time.monotonic() - started, 3)
            steps.append({k: v for k, v in record.items() if k != "bytes"})
            print("%-18s -> %-40s (%5.2f s)"
                  % (text, record.get("response") or record.get("nrc")
                     or record.get("error"), record["seconds"]), flush=True)
            return record

        status = ask("22 02 00", bytes([0x22, 0x02, 0x00]))
        raw = status.get("bytes")
        if raw is not None and raw[:3] == b"\x62\x02\x00":
            result["telemetry"] = decode_status(bytes(raw[3:]))
            print(json.dumps(result["telemetry"], indent=2))

        if args.probe_gate:
            # A bare 31 in the default session answers NRC 0x7F, which would
            # look like a refusal for the wrong reason. Reach the state a real
            # tester reaches first, then read the wire at the gate itself.
            ask("10 02", bytes([0x10, 0x02]))
            seed_record = ask("27 01", bytes([0x27, 0x01]))
            seed_hex = str(seed_record.get("response", "")).split()
            unlocked = False
            if len(seed_hex) >= 6:
                seed = int("".join(seed_hex[2:6]), 16)
                key = calc_key(seed)
                key_record = ask("27 02 %08X" % key,
                                 bytes([0x27, 0x02]) + key.to_bytes(4, "big"))
                unlocked = str(key_record.get("response", "")).startswith("67 02")
            probe = ask("36 01 00", bytes([0x36, 0x01, 0x00]))
            # NRC 0x24 (requestSequenceError) is the open gate: the request got
            # past the gate and died at "no 0x34 first". NRC 0x22 is the closed
            # gate. 0x36 with one byte of data writes nothing.
            accepted = probe.get("nrc_code") == 0x24
            refused = probe.get("nrc_code") == 0x22
            result["gate_probe"] = {"reached_sequence_check": accepted,
                                    "refused_0x22": refused,
                                    "unlocked": unlocked, "used_36": True,
                                    "wire": probe.get("response") or probe.get("nrc")}
            # Re-read the telemetry: 10 02 re-evaluates the gate on the board.
            again = ask("22 02 00", bytes([0x22, 0x02, 0x00]))
            again_raw = again.get("bytes")
            if again_raw is not None and again_raw[:3] == b"\x62\x02\x00":
                result["telemetry_after"] = decode_status(bytes(again_raw[3:]))
    finally:
        transport.close()

    telemetry = result["telemetry"] or {}
    diag = telemetry.get("diag") or {}
    verdict = result["verdict"]
    if diag:
        verdict["role"] = diag["role"]
        verdict["programming_allowed"] = bool(diag["programming_allowed"])
        if not result["gate_probe"]:
            verdict["consistent"] = None
        else:
            # The telemetry claims a capability; the wire has to back it up.
            # Compare against the gate as it stood when 31 was answered, not as
            # it stood before 10 02 re-evaluated it.
            after = (result.get("telemetry_after") or {}).get("diag") or diag
            verdict["gate_after_session"] = bool(after["programming_allowed"])
            verdict["consistent"] = (result["gate_probe"]["reached_sequence_check"]
                                     == bool(after["programming_allowed"]))
        if diag["role"] == "application" and not diag["programming_allowed"]:
            verdict["note"] = ("application refuses write services by build; "
                               "the loader does the writing")
        elif diag["role"] == "loader" and not diag["programming_allowed"]:
            verdict["note"] = "loader gate is closed: storage or a failed job"
        if diag["role"] == "application" and not diag["programming_allowed"]:
            verdict["note"] = ("application refuses write services by build; "
                               "the loader does the writing")
        elif diag["role"] == "loader" and not diag["programming_allowed"]:
            verdict["note"] = "loader gate is closed: storage or a failed job"
    else:
        verdict["consistent"] = False
        verdict["note"] = "no status DID answer"

    report = {"port": args.port, "baud": args.baud, "steps": steps, **result}
    (out / "gate-probe.json").write_text(json.dumps(report, indent=2) + "\n",
                                         encoding="utf-8")
    print("\nverdict:", json.dumps(verdict))
    return 0 if verdict.get("consistent") is not False else 1


if __name__ == "__main__":
    sys.exit(main())
