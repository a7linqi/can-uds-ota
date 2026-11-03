"""Compile optional Explorer BSP + installed ST HAL, without accessing hardware.

Does not assemble or link the Keil startup. Keil semantic checks and ARM
relocatable ELF checks cannot establish that a board boots or CAN works.
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-bin", type=Path, required=True)
    parser.add_argument("--dfp", type=Path, required=True)
    parser.add_argument("--cmsis", type=Path, required=True)
    args = parser.parse_args()
    cc = args.arm_bin / ("armclang.exe" if sys.platform == "win32" else "armclang")
    project = ROOT / "mdk/explorer_readonly.uvprojx"
    build = ROOT / "build/explorer-checks"; build.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / "evidence"; evidence.mkdir(exist_ok=True)
    board = ROOT / "platform/stm32f407/board/explorer_reference"
    expected_includes = [board, ROOT / "include", ROOT / "platform/stm32f407/include",
                         ROOT / "platform/stm32f407/examples", ROOT / "third_party/ed25519",
                         args.dfp / "Drivers/STM32F4xx_HAL_Driver/Inc",
                         args.dfp / "Drivers/CMSIS/Device/ST/STM32F4xx/Include", args.cmsis]
    expected_includes = [p.resolve() for p in expected_includes]
    document = ET.parse(project)
    targets = document.findall("Targets/Target")
    assert len(targets) == 4
    entries = []
    for target in targets:
        name = target.findtext("TargetName")
        capacity = 1048576 if "_ZG_" in name else 524288
        loopback = int("_loopback_" in name)
        controls = target.find("TargetOption/TargetArmAds/Cads/VariousControls")
        defines = controls.findtext("Define").split(",")
        for definition in ("STM32F407xx", "USE_HAL_DRIVER", f"FLASH_TOTAL_SIZE={capacity}u",
                           "OTA_EXPLORER_REFERENCE_PINS=1", f"OTA_EXPLORER_CAN_LOOPBACK={loopback}",
                           "HSE_VALUE=8000000u", "OTA_ENABLE_FLASHING=0",
                           "OTA_ENABLE_BOOT_HANDOFF=0", "OTA_ENABLE_WATCHDOG=0"):
            assert definition in defines, (name, definition)
        includes = [(project.parent / p.replace("\\", "/")).resolve()
                    for p in controls.findtext("IncludePath").split(";")]
        assert includes == expected_includes, "Regenerate the project for these ST/CMSIS pack locations"
        assert all(p.is_dir() for p in includes)
        files = [(project.parent / item.text.replace("\\", "/")).resolve()
                 for item in target.findall("Groups/Group/Files/File/FilePath")]
        assert all(p.is_file() for p in files) and len(files) == len(set(files))
        assert sum(p.name == "main.c" for p in files) == 1
        assert sum(p.name == "startup_stm32f407xx.s" for p in files) == 1
        assert sum(p.name == "system_stm32f4xx.c" for p in files) == 1
        assert not any(p.name in ("board_unconfigured.c", "system_minimal.c", "w25q128_hal_user.c") for p in files)
        entry = ROOT / ("firmware/explorer_loopback/main.c" if loopback else "firmware/bootloader/main.c")
        assert entry in files
        scatter = (project.parent / target.findtext(
            "TargetOption/TargetArmAds/LDads/ScatterFile").replace("\\", "/")).resolve()
        assert "LR_IROM1 0x08000000 0x10000" in scatter.read_text(encoding="utf-8")
        assert target.findtext("TargetOption/TargetCommonOption/SelectedForBatchBuild") == "0"
        directory = build / name; directory.mkdir(exist_ok=True)
        common = [str(cc), "--target=arm-arm-none-eabi", "-mcpu=cortex-m4", "-mfloat-abi=soft",
                  "-std=c99", "-Oz", "-ffunction-sections", "-fdata-sections"]
        common += ["-D" + value for value in defines]
        for path in includes[:4]:
            common += ["-I", str(path)]
        for path in includes[4:]:
            common += ["-isystem", str(path)]
        logs = []
        sources = [p for p in files if p.suffix == ".c"]
        for index, source in enumerate(sources):
            output = directory / f"{index:02d}_{source.stem}.o"
            command = common + ["-Wall", "-Wextra"]
            if source.is_relative_to(ROOT):
                command += ["-Werror"]
            command += ["-c", str(source), "-o", str(output)]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            log = "COMMAND: " + " ".join(command) + "\n" + run.stdout + run.stderr
            logs.append(log)
            if run.returncode:
                (evidence / "explorer-compile-failure.txt").write_text(log, encoding="utf-8")
                print(log)
                return 1
            raw = output.read_bytes()
            assert raw[:5] == b"\x7fELF\x01" and struct.unpack_from("<H", raw, 18)[0] == 40
        (evidence / f"{name}.txt").write_text("\n".join(logs), encoding="utf-8")
        entries.append(dict(target=name, flash_bytes=capacity, can_loopback=bool(loopback),
                            c_objects=len(sources), startup_assembled=False, linked=False))
        print(entries[-1], flush=True)
    # A mismatched target must be rejected at compile time, not silently enable writes.
    checks = []
    for flag in ("OTA_ENABLE_FLASHING", "OTA_ENABLE_BOOT_HANDOFF", "OTA_ENABLE_WATCHDOG"):
        rejected = [x for x in common if not x.startswith("-D" + flag + "=")]
        rejected += ["-D" + flag + "=1", "-fsyntax-only", str(board / "explorer_reference.c")]
        run = subprocess.run(rejected, cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert run.returncode != 0 and "read-only" in run.stderr, (flag, run.stderr)
        checks.append(flag)
    rejected = [x for x in common if not x.startswith("-DOTA_EXPLORER_CAN_LOOPBACK=")]
    rejected += ["-DOTA_EXPLORER_CAN_LOOPBACK=0", "-fsyntax-only", str(ROOT / "firmware/explorer_loopback/main.c")]
    run = subprocess.run(rejected, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert run.returncode != 0 and "physical CAN" in run.stderr
    report = dict(targets=entries, forbidden_gates_rejected=checks,
                  physical_mode_loopback_entry_rejected=True, hardware_verified=False, target_linked=False)
    (evidence / "explorer-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Explorer reference BSP and installed ST HAL C objects passed; hardware/link validation pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
