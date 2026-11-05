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
    payload[42]  pending_in_flight (a 7F xx 78 is on the wire)
    payload[43]  bit7 = this image is the application, bits 0-6 = deadline misses

This script reads them and, with --probe-gate, proves the verdict by sending a
real gated request that is cheap and non-destructive:

    31 01 00 01   check the download slot; erases nothing

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
            "pending_in_flight": diag[2],
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
            probe = ask("31 01 00 01", bytes([0x31, 0x01, 0x00, 0x01]))
            accepted = str(probe.get("response", "")).startswith("71 01 00 01")
            refused = probe.get("nrc") is not None and "0x22" in str(probe.get("nrc"))
            result["gate_probe"] = {"accepted": accepted, "refused_0x22": refused,
                                    "wire": probe.get("response") or probe.get("nrc")}
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
            verdict["consistent"] = (result["gate_probe"]["accepted"]
                                     == bool(diag["programming_allowed"]))
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
