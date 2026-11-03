"""Validate L2 adapters, project layouts and optional ARM object compilation."""
import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--cc", required=True, help="Host C compiler executable")
parser.add_argument("--in-memory", action="store_true", help="TinyCC native in-process execution")
parser.add_argument("--arm-bin", type=Path, help="ARM Compiler 6 bin directory; C object checks, not full link")
parser.add_argument("--dfp", type=Path, help="Optional installed Keil STM32F4xx_DFP root for HAL syntax check")
parser.add_argument("--cmsis", type=Path, help="CMSIS core Include directory")
args = parser.parse_args()
evidence = ROOT / "evidence"
build = ROOT / "build/l2_checks"
evidence.mkdir(exist_ok=True)
build.mkdir(parents=True, exist_ok=True)
result = {"hardware_verified": False, "target_linked": False}
core = sorted((ROOT / "src/core").glob("*.c"))
drivers = sorted((ROOT / "src/driver").glob("*.c"))
adapters = sorted((ROOT / "platform/stm32f407/src").glob("*.c"))
ed25519 = sorted((ROOT / "third_party/ed25519").glob("*.c"))
portable = [p for p in adapters if p.name not in ("stm32_arch.c", "system_minimal.c")]

def execute(command, log):
    run = subprocess.run([str(x) for x in command], cwd=ROOT, capture_output=True, text=True)
    content = "COMMAND: " + " ".join(map(str, command)) + "\n" + run.stdout + run.stderr
    log.write_text(content, encoding="utf-8")
    if run.returncode:
        print(content)
        raise SystemExit(run.returncode)
    return run.stdout + run.stderr

# Compile disabled and enabled gates for both flash capacities.
result["host_adapters"] = []
for capacity in (1024, 512):
    for enabled in (0, 1):
        command = [args.cc, "-std=c99", "-Wall", "-Wextra", "-Iinclude",
                   "-Iplatform/stm32f407/include", "-Itests_stm32", "-Ithird_party/ed25519",
                   f"-DFLASH_TOTAL_SIZE={capacity*1024}u",
                   f"-DOTA_ENABLE_FLASHING={enabled}", f"-DOTA_ENABLE_BOOT_HANDOFF={enabled}",
                   f"-DOTA_ENABLE_WATCHDOG={enabled}"]
        sources = core + drivers + portable + ed25519 + [ROOT / "tests_stm32/mock_board.c"]
        command += [str(p.relative_to(ROOT)) for p in sources]
        if args.in_memory:
            if Path(args.cc).stem.lower() != "tcc":
                parser.error("--in-memory requires TinyCC")
            command += ["-run", "tests_stm32/test_stm32.c"]
        else:
            binary = build / f"adapters_{capacity}_{enabled}{'.exe' if sys.platform=='win32' else ''}"
            command += ["tests_stm32/test_stm32.c", "-o", str(binary)]
        name = f"stm32-host-{capacity}-gates{enabled}.txt"
        output = execute(command, evidence / name)
        if not args.in_memory:
            output += execute([binary], evidence / name.replace(".txt", "-run.txt"))
        match = re.search(r"STM32 adapter mocks: (\d+) checks, (\d+) failures", output)
        if not match or int(match.group(2)):
            raise SystemExit("Missing/failed C adapter result")
        entry = {"flash_kib": capacity, "gates_enabled": enabled,
                 "checks": int(match.group(1)), "failures": 0}
        result["host_adapters"].append(entry)
        print(entry, flush=True)

# Semantic validation against actual source paths, defines and scatter ranges.
projects = []
for image in ("bootloader", "app"):
    project = ROOT / "mdk" / f"{image}.uvprojx"
    document = ET.parse(project)
    for target in document.findall("Targets/Target"):
        name = target.findtext("TargetName")
        part = "ZG" if "_ZG_" in name else "ZE"
        capacity = 0x100000 if part == "ZG" else 0x80000
        base, length = (0x08000000, 0x10000) if image == "bootloader" else (0x08010000, capacity-0x10000)
        assert target.findtext("uAC6") == "1"
        defines = target.findtext("TargetOption/TargetArmAds/Cads/VariousControls/Define")
        assert f"FLASH_TOTAL_SIZE={capacity}u" in defines
        assert "OTA_ENABLE_FLASHING=0" in defines and "OTA_ENABLE_BOOT_HANDOFF=0" in defines
        assert "OTA_ENABLE_WATCHDOG=0" in defines
        listed = [item.text for item in target.findall("Groups/Group/Files/File/FilePath")]
        resolved = [(project.parent / s.replace("\\", "/")).resolve() for s in listed]
        assert all(p.is_relative_to(ROOT) and p.is_file() for p in resolved)
        assert ROOT / f"firmware/{image}/main.c" in resolved
        assert ROOT / "platform/stm32f407/board/board_unconfigured.c" in resolved
        assert ROOT / "src/driver/spi_nor.c" in resolved
        assert not any("examples/board_hal_binding.c" in p.as_posix() for p in resolved)
        scatter_name = target.findtext("TargetOption/TargetArmAds/LDads/ScatterFile")
        scatter = (project.parent / scatter_name.replace("\\", "/")).resolve()
        text = scatter.read_text(encoding="utf-8")
        assert f"LR_IROM1 0x{base:08X} 0x{length:X}" in text
        assert f"ER_IROM1 0x{base:08X} 0x{length:X}" in text
        assert "startup_stm32f407xx.o (RESET, +First)" in text
        assert "RW_IRAM1 0x20000000 0x20000" in text
        projects.append({"name": name, "sources": len(resolved), "base": hex(base), "length": hex(length)})
result["project_semantics"] = projects
print("Keil source paths, scatter ranges, default gates: 4 targets passed", flush=True)

if args.arm_bin:
    cc = args.arm_bin / ("armclang.exe" if sys.platform == "win32" else "armclang")
    if not cc.is_file():
        parser.error("armclang not found")
    result["arm_objects"] = []
    for capacity in (1024, 512):
        for image in ("bootloader", "app"):
            directory = build / f"arm-{capacity}-{image}"
            directory.mkdir(exist_ok=True)
            logs = []
            sources = core + drivers + adapters + ed25519 + [ROOT / "platform/stm32f407/board/board_unconfigured.c",
                                          ROOT / f"firmware/{image}/main.c"]
            for index, source in enumerate(sources):
                output = directory / f"{index:02d}_{source.stem}.o"
                # Enabled branches checked as well; actual Keil defaults stay disabled.
                command = [cc, "--target=arm-arm-none-eabi", "-mcpu=cortex-m4", "-mfloat-abi=soft",
                           "-std=c99", "-Oz", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
                           "-Iinclude", "-Iplatform/stm32f407/include", "-Ithird_party/ed25519",
                           f"-DFLASH_TOTAL_SIZE={capacity*1024}u",
                           "-DOTA_ENABLE_FLASHING=1", "-DOTA_ENABLE_BOOT_HANDOFF=1", "-DOTA_ENABLE_WATCHDOG=1",
                           "-c", source, "-o", output]
                log = directory / f"{index:02d}.txt"
                execute(command, log)
                data = output.read_bytes()
                assert data[:4] == b"\x7fELF" and data[4] == 1
                assert struct.unpack_from("<H", data, 18)[0] == 40  # EM_ARM
                logs.append(log.read_text(encoding="utf-8"))
            (evidence / f"stm32-arm-{capacity}-{image}.txt").write_text("\n".join(logs), encoding="utf-8")
            result["arm_objects"].append({"flash_kib": capacity, "image": image, "objects": len(sources), "exit": 0})
            print(result["arm_objects"][-1], flush=True)
    if args.dfp and args.cmsis:
        config = build / "hal_config"
        config.mkdir(exist_ok=True)
        shutil.copyfile(args.dfp / "Drivers/STM32F4xx_HAL_Driver/Inc/stm32f4xx_hal_conf_template.h",
                        config / "stm32f4xx_hal_conf.h")
        includes = [ROOT / "include", ROOT / "platform/stm32f407/include", ROOT / "platform/stm32f407/examples"]
        system_includes = [config, args.dfp / "Drivers/STM32F4xx_HAL_Driver/Inc",
                           args.dfp / "Drivers/CMSIS/Device/ST/STM32F4xx/Include", args.cmsis]
        command = [cc, "--target=arm-arm-none-eabi", "-mcpu=cortex-m4", "-std=c99",
                   "-Wall", "-Wextra", "-Werror", "-fsyntax-only", "-DSTM32F407xx", "-DUSE_HAL_DRIVER"]
        command += ["-I" + str(p) for p in includes]
        for path in system_includes:
            command += ["-isystem", path]
        command += ["platform/stm32f407/examples/board_hal_binding.c",
                    "platform/stm32f407/examples/w25q128_hal_user.c"]
        execute(command, evidence / "stm32-hal-reference.txt")
        result["hal_reference_syntax"] = {"exit": 0, "linked": False}
        print("HAL reference syntax passed", flush=True)

(evidence / "stm32-results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print("L2 preparation checks passed; hardware/target-link validation remains pending.")
