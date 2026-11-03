"""Aggregate R6/R2 checks: opcode audit, C state-machine test, fake-CLI test,
success-then-failed rerun isolation, and the positive two-pass simulation."""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can\ota-core")
TCC = r"D:\Project\Can\toolchain\tccbox-2025.10.27.data\purelib\tccbox\tcc_dist\tcc.exe"


def opcode_audit():
    """Static check: the firmware emits only the 0x03 read opcode (0x9F is the
    reference BSP's read-ID during init); no write/erase opcodes anywhere."""
    main = (LAB / "firmware/nor_backup_main.c").read_text(encoding="utf-8")
    stream = (LAB / "firmware/nor_stream.c").read_text(encoding="utf-8")
    text = main + stream
    assert "cmd[0] = 0x03u" in main, "0x03 read opcode not found"
    for op in ("0x06", "0x02", "0x20", "0xD8", "0xC7", "0x60", "0x01"):
        assert op not in text, f"forbidden write/erase opcode {op} present"
    print("  opcode audit: only 0x03 read (no write/erase): OK")


def run_cmd(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"cmd failed {r.returncode}: {r.stdout}{r.stderr}")
    return r.stdout


def c_state_machine():
    run_cmd([TCC, "-std=c99", "-Wall", "-DNOR_TOTAL_SIZE=24576u",
             "-I", str(LAB / "firmware"), "-I", str(CORE / "include"),
             str(LAB / "firmware/nor_stream.c"), str(CORE / "src/core/crc32.c"),
             "-run", str(LAB / "tests/test_nor_stream.c")])
    print("  C state machine (publish order/SPI/ACK/reset): OK")


def fake_cli():
    run_cmd([sys.executable, str(LAB / "tests/test_nor_host_fakecli.py")])
    print("  fake-CLI adapter paths: OK")


def rerun_isolation():
    """Success then failed rerun must not touch the earlier run's files."""
    spec = importlib.util.spec_from_file_location("nor_host", LAB / "host/nor_backup.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    original = host.FirmwareLike
    fixtures = LAB / "build/check-fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    out_base = Path(tempfile.mkdtemp(prefix="rerun-", dir=fixtures))
    # First run: success (small model capacity).
    with mock.patch.object(host, "NOR_TOTAL_SIZE", 64):
        with mock.patch.object(host, "datetime") as dt:
            fixed = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dt.now.return_value = datetime(2026, 11, 2, 1, 2, 3, 111111)
        r1 = host.main(["--out-base", str(out_base)])
        dirs1 = sorted(d for d in out_base.iterdir() if d.is_dir())
        assert r1 == 0 and len(dirs1) == 1, (r1, dirs1)
        first_dir = dirs1[0]
        first_pass1 = (first_dir / "pass1" / "pass1.bin").read_bytes()
        first_manifest = json.loads((first_dir / "manifest.json").read_text())
        assert first_manifest["status"] == "passed", first_manifest
    # Second run: failure (bad CRC injected).
    with mock.patch.object(host, "NOR_TOTAL_SIZE", 64):
        with mock.patch.object(host, "FirmwareLike",
                               side_effect=lambda pattern: original(pattern, inject_bad_crc_at=0)):
            r2 = host.main(["--out-base", str(out_base)])
        dirs2 = sorted(d for d in out_base.iterdir() if d.is_dir())
        assert r2 == 1 and len(dirs2) == 2, (r2, dirs2)
        assert (first_dir / "pass1" / "pass1.bin").read_bytes() == first_pass1, "historical bytes changed"
        assert json.loads((first_dir / "manifest.json").read_text()) == first_manifest, "old manifest overwritten"
        second_manifest = json.loads((dirs2[1] / "manifest.json").read_text())
        assert second_manifest["status"] == "partial", second_manifest
    print("  success-then-failed rerun isolation: OK")


def positive_simulation():
    r = subprocess.run([sys.executable, str(LAB / "host/nor_backup.py"),
                        "--out-base", str(LAB / "evidence/nor-sim-final")],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise AssertionError("positive simulation failed: " + r.stdout + r.stderr)
    print("  positive two-pass simulation (16 MiB x2): OK")


def main():
    print("[board-lab R6/R2 aggregate checks]")
    opcode_audit()
    c_state_machine()
    fake_cli()
    rerun_isolation()
    positive_simulation()
    sys.path.insert(0, str(LAB))
    from build_nor import stage_hashes, check_core_frozen
    (LAB / "evidence").mkdir(exist_ok=True)
    (LAB / "evidence/review-checks.json").write_text(json.dumps({"status": "passed", "stage_input_sha256": stage_hashes(), "core_hashes": check_core_frozen(), "hardware_executed": False}, indent=2)+"\n", encoding="utf-8")
    print("ALL AGGREGATE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
