"""Aggregate checks for the NOR restore drill.

Runs, in order: the W25Q driver test, the restore state-machine test, the host
tool selftest, the end-to-end power-cut matrix, and the full 16 MiB restore of
the real golden dump in the simulator. Writes one evidence record that states
plainly that no hardware was executed.
"""
import json
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can\ota-core")
TCC = r"D:\Project\Can\toolchain\tccbox-2025.10.27.data\purelib\tccbox\tcc_dist\tcc.exe"
PY = sys.executable
GOLDEN = Path(r"D:\Project\Can\backups\nor-original\full\20261103_200408_779736\pass1\pass1.bin")
GOLDEN_SHA = "929af96ac660590418cb74878033b742bcd31d9e7108933ce769548f40e305e1"
GOLDEN_CRC = "0xa4188821"


def run(cmd, timeout=900):
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"command failed ({proc.returncode}): "
                             f"{' '.join(map(str, cmd))}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def c_tests():
    base = [TCC, "-std=c99", "-Wall", "-Wextra", "-I", LAB / "firmware",
            "-I", CORE / "include"]
    run(base + [LAB / "firmware/w25q_bench.c", CORE / "src/core/crc32.c",
                "-run", LAB / "tests/test_w25q_bench.c"])
    run(base + [LAB / "firmware/nor_restore.c", CORE / "src/core/crc32.c",
                "-run", LAB / "tests/test_nor_restore_c.c"])
    print("  C driver + state machine tests: OK")


def host_selftest():
    run([PY, LAB / "host/nor_restore.py", "--selftest"])
    print("  host selftest: OK")


def host_tests():
    run([PY, LAB / "tests/test_nor_restore_host.py"])
    print("  end-to-end power-cut matrix + CLI checks: OK")


def golden_simulation():
    if not GOLDEN.is_file():
        raise AssertionError(f"golden dump missing: {GOLDEN}")
    out = LAB / "evidence/nor-restore-sim"
    run([PY, LAB / "host/nor_restore.py", "--image", GOLDEN,
         "--expect-sha256", GOLDEN_SHA, "--expect-crc32", GOLDEN_CRC,
         "--out-base", out])
    latest = sorted(p for p in out.iterdir() if p.is_dir())[-1]
    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "passed", manifest
    assert manifest["chip_matches_image"] is True, manifest
    assert manifest["device_crc32"] == int(GOLDEN_CRC, 16), manifest
    assert manifest["program_without_erase"] == 0, manifest
    print(f"  16 MiB golden restore in sim: {manifest['sectors_erased']} sectors, "
          f"{manifest['pages_programmed']} pages, CRC {manifest['device_crc32']:08x}")
    return {"run_dir": latest.name, "manifest": manifest}


def main():
    print("[board-lab restore drill checks]")
    c_tests()
    host_selftest()
    host_tests()
    golden = golden_simulation()
    sys.path.insert(0, str(LAB))
    from build_restore import stage_hashes, check_core_frozen
    record = {
        "status": "passed",
        "stage": "nor-restore",
        "stage_input_sha256": stage_hashes(),
        "core_hashes": check_core_frozen(),
        "hardware_executed": False,
        "golden": {"path": str(GOLDEN), "sha256": GOLDEN_SHA, "crc32": GOLDEN_CRC},
        "simulation": {"run_dir": golden["run_dir"],
                       "blocks": golden["manifest"]["blocks"],
                       "sectors_erased": golden["manifest"]["sectors_erased"],
                       "pages_programmed": golden["manifest"]["pages_programmed"],
                       "chip_sha256": golden["manifest"]["chip_sha256"]},
        "power_cut_matrix": "every block x {erase, program, verify} on a 256 KiB image, "
                            "plus one cut during the final CRC sweep",
    }
    (LAB / "evidence/nor-restore-checks.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("ALL RESTORE DRILL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
