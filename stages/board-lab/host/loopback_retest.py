"""Group B: retest the already-flashed ZG loopback firmware across 3 resets.

Reads the probe structure over SWD after each reset and checks every
acceptance criterion. Original CLI bytes are preserved; UTF-8 copies are added.
"""
import hashlib
import json
import re
import struct
import subprocess
import time
import zlib
from pathlib import Path

CLI = r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
LOOPBACK_BIN = r"D:\Project\Can\ota-core\build\gnu-explorer\explorer_ZG_loopback_READONLY\explorer_ZG_loopback_READONLY.bin"
BACKUP_DIR = Path(r"D:\Project\Can\backups\board-original\20261102_204315_470")
PHASE2_EEPROM = Path(r"D:\Project\Can\stages\app-bringup\evidence\phase2-eeprom-snapshot-256.bin")
OUT_BASE = Path(r"D:\Project\Can\stages\board-lab\evidence\loopback-retest")

EXPECTED = {
    "loopback_sha256": "15a908c096bd2b264be5ff32d680f90508c98d2f94e4d91cd7d7ee7bd41c970a",
    "loopback_bytes": 12736,
    "backup_sha256": "a919efd8201a2d86b479baeb7ab327a90c28eaefd443b937364f42562b7e367c",
    "backup_bytes": 1048576,
    "phase2_eeprom_sha256": "40aff2e9d2d8922e47afd4648e6967497158785fbd1da870e7110266bf944880",
    "nor_jedec": 0x00EF4018,
    "stage": 3,
    "passed": 16,
}


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run_cli(args, log_path):
    proc = subprocess.run([CLI] + args, capture_output=True, timeout=120)
    raw = proc.stdout + b"\n" + proc.stderr
    log_path.write_bytes(raw)
    # Also keep a UTF-8 copy (best effort decode of whatever encoding CLI used).
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    Path(str(log_path) + ".utf8").write_text(text, encoding="utf-8")
    return proc.returncode, text


def main():
    # --- Precondition: hashes ---
    assert Path(LOOPBACK_BIN).stat().st_size == EXPECTED["loopback_bytes"]
    assert sha256(LOOPBACK_BIN) == EXPECTED["loopback_sha256"], "loopback BIN hash mismatch"
    for name in ("internal-flash-original.bin", "internal-flash-readback.bin"):
        p = BACKUP_DIR / name
        assert p.stat().st_size == EXPECTED["backup_bytes"]
        assert sha256(p) == EXPECTED["backup_sha256"], "backup hash mismatch"
    phase2_eeprom_sha = sha256(PHASE2_EEPROM)
    assert phase2_eeprom_sha == EXPECTED["phase2_eeprom_sha256"], "phase2 eeprom snapshot mismatch"

    results = []
    for rnd in range(1, 4):
        d = OUT_BASE / f"round{rnd}"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"round": rnd, "dir": str(d)}

        # reset
        rc, text = run_cli(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000", "-rst"],
                           d / "reset.log")
        entry["reset_exit"] = rc
        time.sleep(2.0)

        # read stage/passed + full struct
        struct_path = d / "probe-struct.bin"
        rc, text = run_cli(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                            "-r32", "0x20000254", "0x8",
                            "-u", "0x2000000C", "0x128", str(struct_path)],
                           d / "probe.log")
        entry["read_exit"] = rc
        m = re.search(r"0x20000254\s*:\s*([0-9A-Fa-f]{8})\s*([0-9A-Fa-f]{8})", text)
        if not m:
            entry["error"] = "stage/passed line not found"
            entry["passed_checks"] = False
            results.append(entry)
            continue
        stage, passed = int(m.group(1), 16), int(m.group(2), 16)
        entry["stage"] = stage
        entry["passed"] = passed

        raw = struct_path.read_bytes()
        entry["struct_bytes"] = len(raw)
        if len(raw) != 296:
            entry["error"] = "struct not 296 bytes"
            entry["passed_checks"] = False
            results.append(entry)
            continue
        f = struct.unpack("<8I2i256s", raw)
        buf = f[10]
        crc = zlib.crc32(buf) & 0xFFFFFFFF
        entry["device_id"] = hex(f[0])
        entry["nor_jedec"] = "0x%08X" % f[6]
        entry["nor_status"] = f[8]
        entry["eeprom_status"] = f[9]
        entry["eeprom_crc_field"] = "0x%08X" % f[7]
        entry["eeprom_crc_recomputed"] = "0x%08X" % crc
        entry["eeprom_crc_matches"] = crc == f[7]
        snap = d / "eeprom-snapshot-256.bin"
        snap.write_bytes(buf)
        entry["eeprom_sha256"] = sha256(snap)
        entry["eeprom_same_as_phase2"] = entry["eeprom_sha256"] == phase2_eeprom_sha

        checks = {
            "stage==3": stage == EXPECTED["stage"],
            "passed==16": passed == EXPECTED["passed"],
            "nor_jedec==EF4018": f[6] == EXPECTED["nor_jedec"],
            "nor_status==0": f[8] == 0,
            "eeprom_status==0": f[9] == 0,
            "eeprom_crc_match": crc == f[7],
            "eeprom_same_as_phase2": entry["eeprom_same_as_phase2"],
        }
        entry["checks"] = checks
        entry["passed_checks"] = all(checks.values())
        results.append(entry)
        print(f"round {rnd}: stage={stage} passed={passed} nor={entry['nor_jedec']} "
              f"eeprom_ok={f[9]==0 and crc==f[7]} all={entry['passed_checks']}")

    summary = {
        "rounds": results,
        "all_passed": all(r.get("passed_checks") for r in results),
        "expectations": EXPECTED,
    }
    (OUT_BASE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("ALL PASSED:", summary["all_passed"])
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
