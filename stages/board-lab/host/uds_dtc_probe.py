"""Exercise 0x19 / 0x14 over the real UART link, on the real board.

The DTC table (8 entries, RAM only) was implemented on 2026-11-05 and covered
by host-model assertions, but until this script ran nobody had ever seen a
0x19 answer come back from silicon. Everything below is chosen so the probe
never writes flash, never changes the installed image and never moves the
metadata state machine:

    boundary      19 (2 bytes)      -> NRC 0x13   (wrong length)
                  19 03 FF          -> NRC 0x12   (sub-function not supported)
                  14 FF FF          -> NRC 0x13   (groupOfDTC is 3 bytes)
                  14 00 00 00       -> NRC 0x31   (only FF FF FF exists)
    baseline      19 02 FF / 19 01 08
    trigger       27 01 / 27 02 <wrong key> x3  -> the third answers NRC 0x36
                  and the handler calls uds_dtc_record(UDS_DTC_SECURITY)
    read back     19 02 FF must now contain 0xC10400
                  19 01 08 must report count = 1
    clear         14 FF FF FF -> 0x54
    read back     19 02 FF is empty again

The only state this leaves behind is a cleared DTC table and, if the board was
already unlocked, the three failed keys that re-lock it for 10 s. Nothing is
persisted: the table dies with the next reset.

    python host/uds_dtc_probe.py --port COM9 --out evidence/<dir>
"""
import argparse
import json
import sys
import time
from pathlib import Path

CORE = Path(r"D:\Project\Can-learning\ota-core")
sys.path.insert(0, str(CORE / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pc.transport import SerialCanTransport  # noqa: E402
from pc.isotp_client import IsoTpClient  # noqa: E402
from pc.uds_client import UdsClient, NegativeResponse  # noqa: E402
# Same key schedule as the board. A hand-rolled XOR here answers NRC 0x35 and
# the failure looks like a locked server instead of a wrong test.
from uds_stage_d import calc_key  # noqa: E402

DTC_SECURITY = 0xC10400
DTC_NAMES = {
    0xC10100: "external NOR erase / program / read",
    0xC10200: "package header / CRC / signature",
    0xC10300: "metadata_store read or write",
    0xC10400: "three consecutive invalid keys",
}
HISTORY = (0x01 | 0x02 | 0x08 | 0x20)
HISTORY_CONFIRMED = (0x08 | 0x20)


def parse_dtc(answer):
    """59 <sub> <availability> ( <status> <code:3> )* """
    if len(answer) < 3 or answer[0] != 0x59:
        raise ValueError("not a 0x19 answer: %s" % answer.hex(" "))
    body = answer[3:]
    if len(body) % 4 != 0:
        raise ValueError("answer carries a partial record: %s" % answer.hex(" "))
    records = []
    for i in range(0, len(body), 4):
        code = int.from_bytes(body[i + 1:i + 4], "big")
        records.append({"code": "0x%06X" % code,
                        "name": DTC_NAMES.get(code, "unknown"),
                        "status": "0x%02X" % body[i]})
    return records


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = []
    result = {"boundary": [], "dtc": {}}
    transport = SerialCanTransport(args.port, args.baud)

    try:
        client = UdsClient(IsoTpClient(transport), lambda **kw: None,
                           total_timeout=120)

        def ask(text, payload, expect_nrc=None, allow_nrc=False):
            started = time.monotonic()
            record = {"request": text}
            try:
                answer = client.request(payload)
                record["response"] = answer.hex(" ").upper()
                record["bytes"] = answer
            except NegativeResponse as error:
                record["nrc_code"] = int(error.nrc)
                record["nrc"] = "0x%02X" % int(error.nrc)
            except Exception as error:  # timeout, framing, anything else
                record["error"] = str(error)
            record["seconds"] = round(time.monotonic() - started, 3)
            if expect_nrc is not None:
                record["expected_nrc"] = "0x%02X" % expect_nrc
                record["ok"] = record.get("nrc_code") == expect_nrc
            steps.append({k: v for k, v in record.items() if k != "bytes"})
            print("%-22s -> %-44s (%5.2f s)"
                  % (text, record.get("response") or record.get("nrc")
                     or record.get("error"), record["seconds"]), flush=True)
            if allow_nrc:
                return record
            return record

        def read_dtc(label):
            record = ask(label, bytes([0x19, 0x02, HISTORY]))
            raw = record.get("bytes")
            if raw is None:
                record["records"] = None
                return record
            record["records"] = parse_dtc(bytes(raw))
            return record

        # ---- baseline, before anything is provoked ----
        status = ask("22 02 00", bytes([0x22, 0x02, 0x00]))
        result["status"] = (status.get("response") or status.get("nrc")
                            or status.get("error"))

        # ---- boundary answers (no state change) ----
        result["boundary"].append(
            ask("19", bytes([0x19]), expect_nrc=0x13))
        result["boundary"].append(
            ask("19 03 FF", bytes([0x19, 0x03, HISTORY]), expect_nrc=0x12))
        result["boundary"].append(
            ask("14 FF FF", bytes([0x14, 0xFF, 0xFF]), expect_nrc=0x13))
        result["boundary"].append(
            ask("14 00 00 00", bytes([0x14, 0x00, 0x00, 0x00]), expect_nrc=0x31))

        # ---- baseline table ----
        before = read_dtc("19 02 FF")
        result["dtc"]["before"] = before.get("records")
        count_before = ask("19 01 08", bytes([0x19, 0x01, HISTORY_CONFIRMED]))
        raw = count_before.get("bytes")
        result["dtc"]["count_before"] = (int.from_bytes(raw[4:6], "big")
                                        if raw and len(raw) >= 6 else None)

        # ---- provoke one security DTC: three consecutive wrong keys ----
        # The third failure is what calls uds_dtc_record(); the first two
        # answer NRC 0x35 (invalidKey). A board that is already locked answers
        # the very first 27 01 with 0x36, so wait out the 10 s window first.
        result["trigger"] = []
        for attempt in range(3):
            seed_record = ask("27 01", bytes([0x27, 0x01]))
            seed_raw = seed_record.get("bytes")
            if seed_record.get("nrc_code") == 0x36:
                print("server is locked out, waiting 11 s", flush=True)
                time.sleep(11.0)
                seed_record = ask("27 01", bytes([0x27, 0x01]))
                seed_raw = seed_record.get("bytes")
            if not seed_raw or len(seed_raw) < 6:
                result["trigger"].append({"aborted": "no seed"})
                break
            seed = int.from_bytes(bytes(seed_raw[2:6]), "big")
            wrong = calc_key(seed) ^ 0xFFFFFFFF
            expected = 0x36 if attempt == 2 else 0x35
            result["trigger"].append(
                ask("27 02 %08X (wrong)" % wrong,
                    bytes([0x27, 0x02]) + wrong.to_bytes(4, "big"),
                    expect_nrc=expected))

        # ---- read it back ----
        after = read_dtc("19 02 FF")
        result["dtc"]["after"] = after.get("records")
        count_after = ask("19 01 08", bytes([0x19, 0x01, HISTORY_CONFIRMED]))
        raw = count_after.get("bytes")
        result["dtc"]["count_after"] = (int.from_bytes(raw[4:6], "big")
                                       if raw and len(raw) >= 6 else None)

        # ---- clear and prove it ----
        cleared = ask("14 FF FF FF", bytes([0x14, 0xFF, 0xFF, 0xFF]))
        result["dtc"]["clear"] = cleared.get("response") or cleared.get("nrc")
        final = read_dtc("19 02 FF")
        result["dtc"]["final"] = final.get("records")
        count_final = ask("19 01 08", bytes([0x19, 0x01, HISTORY_CONFIRMED]))
        raw = count_final.get("bytes")
        result["dtc"]["count_final"] = (int.from_bytes(raw[4:6], "big")
                                       if raw and len(raw) >= 6 else None)
    finally:
        transport.close()

    after_codes = [r["code"] for r in (result["dtc"].get("after") or [])]
    final_codes = [r["code"] for r in (result["dtc"].get("final") or [])]
    verdict = {
        "boundary_all_ok": all(r.get("ok") for r in result["boundary"]),
        "security_dtc_recorded": ("0x%06X" % DTC_SECURITY) in after_codes,
        "count_went_up": (result["dtc"].get("count_after") or 0)
                         > (result["dtc"].get("count_before") or 0),
        "clear_answered_0x54": result["dtc"].get("clear") == "54",
        "table_empty_after_clear": result["dtc"].get("count_final") == 0,
        "final_codes": final_codes,
    }
    verdict["passed"] = all(verdict[k] for k in (
        "boundary_all_ok", "security_dtc_recorded", "count_went_up",
        "clear_answered_0x54", "table_empty_after_clear"))

    report = {"port": args.port, "baud": args.baud, "steps": steps,
              **result, "verdict": verdict}
    (out / "dtc-probe.json").write_text(json.dumps(report, indent=2) + "\n",
                                        encoding="utf-8")
    print("\nverdict:", json.dumps(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
