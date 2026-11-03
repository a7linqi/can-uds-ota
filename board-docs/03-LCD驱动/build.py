"""Build the standalone LCD probe image.

Deliberately kept OUT of stages/board-lab: that stage's build snapshot hashes
every .c/.h/.py/.ld/.ps1 under its own tree, so adding probe sources there
would invalidate the approved NOR-backup build record. This directory owns
only the probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = Path(r"D:\Project\Can\ota-core")
UVPROJX = PROJECT / "mdk" / "explorer_readonly.uvprojx"
TARGET_NAME = "explorer_ZG_loopback_READONLY"
IMAGE = "lcd_probe_READONLY"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(args) -> int:
    gcc = Path(args.gcc)
    dfp = Path(args.dfp)

    target = next(t for t in ET.parse(UVPROJX).findall("Targets/Target")
                  if t.findtext("TargetName") == TARGET_NAME)
    controls = target.find("TargetOption/TargetArmAds/Cads/VariousControls")
    defines = [d for d in controls.findtext("Define").split(",") if d != "__MICROLIB"]
    # The probe must never enable update paths; force the gates off explicitly.
    for flag in ("OTA_ENABLE_FLASHING", "OTA_ENABLE_BOOT_HANDOFF", "OTA_ENABLE_WATCHDOG"):
        defines = [d for d in defines if not d.startswith(flag + "=")]
        defines.append(flag + "=0")

    includes = [(UVPROJX.parent / p.replace("\\", "/")).resolve()
                for p in controls.findtext("IncludePath").split(";")]

    # The probe deliberately does NOT link the OTA core or the reference BSP:
    # it needs only the HAL subset below, the FSMC low-level driver, the CMSIS
    # system file and the startup code. Keeping the set minimal also keeps the
    # probe from pulling in unrelated boot/CAN symbols.
    hal_src = dfp / "Drivers/STM32F4xx_HAL_Driver/Src"
    hal_names = (
        "stm32f4xx_hal.c",
        "stm32f4xx_hal_cortex.c",
        "stm32f4xx_hal_rcc.c",
        "stm32f4xx_hal_rcc_ex.c",
        "stm32f4xx_hal_gpio.c",
        "stm32f4xx_hal_pwr.c",
        "stm32f4xx_hal_pwr_ex.c",
        "stm32f4xx_hal_flash.c",
        "stm32f4xx_hal_flash_ex.c",
        "stm32f4xx_hal_dma.c",
        "stm32f4xx_hal_sram.c",
        "stm32f4xx_ll_fsmc.c",
    )
    sources = [ROOT / "main.c", PROJECT / "firmware/runtime/newlib_init.c"]
    for name in hal_names:
        path = hal_src / name
        if not path.is_file():
            raise SystemExit(f"HAL source missing: {path}")
        sources.append(path)

    system = dfp / "Drivers/CMSIS/Device/ST/STM32F4xx/Source/Templates/system_stm32f4xx.c"
    startup = system.parent / "gcc/startup_stm32f407xx.s"
    if not system.is_file() or not startup.is_file():
        raise SystemExit(f"ST system/startup missing under {system.parent}")
    sources += [system, startup]

    out = ROOT / "build" / IMAGE
    out.mkdir(parents=True, exist_ok=True)
    objcopy = gcc.with_name("arm-none-eabi-objcopy" + gcc.suffix)
    nm = gcc.with_name("arm-none-eabi-nm" + gcc.suffix)

    common = [str(gcc), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
              "-Os", "-g3", "-ffunction-sections", "-fdata-sections",
              "-fno-common", "-std=c99"]
    common += ["-D" + d for d in defines]
    for path in [ROOT, PROJECT, *includes[:5]]:
        common += ["-I", str(path)]
    for path in includes[5:]:
        common += ["-isystem", str(path)]

    logs: list[str] = []
    objects: list[Path] = []
    for index, source in enumerate(sources):
        obj = out / f"{index:02d}_{source.stem}.o"
        flags = ["-Wall", "-Wextra"] if source.suffix == ".c" else []
        cmd = common + flags + ["-c", str(source), "-o", str(obj)]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        logs.append("COMMAND: " + " ".join(cmd) + "\n" + run.stdout + run.stderr)
        if run.returncode != 0:
            print("\n".join(logs[-2:]))
            raise SystemExit(f"compile failed: {source}")
        objects.append(obj)

    elf = out / (IMAGE + ".elf")
    binary = out / (IMAGE + ".bin")
    mapfile = out / (IMAGE + ".map")
    link = [str(gcc), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
            "-nostartfiles", "--specs=nano.specs", "--specs=nosys.specs",
            "-T", str(ROOT / "link.ld"), "-Wl,--gc-sections",
            "-Wl,--fatal-warnings", "-Wl,-Map=" + str(mapfile),
            *[str(o) for o in objects], "-o", str(elf)]
    run = subprocess.run(link, capture_output=True, text=True, timeout=180)
    logs.append("COMMAND: " + " ".join(link) + "\n" + run.stdout + run.stderr)
    if run.returncode != 0:
        print("\n".join(logs[-2:]))
        raise SystemExit("link failed")

    subprocess.run([str(objcopy), "-O", "binary", str(elf), str(binary)], check=True)

    symbols: dict[str, int] = {}
    nm_out = subprocess.run([str(nm), "-n", "-S", str(elf)],
                            capture_output=True, text=True, check=True).stdout
    for line in nm_out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch("[0-9a-fA-F]+", parts[0]):
            symbols[parts[-1]] = int(parts[0], 16)

    report = {
        "image": IMAGE,
        "objects": len(objects),
        "bin_bytes": binary.stat().st_size,
        "bin_sha256": digest(binary),
        "elf_sha256": digest(elf),
        "symbols": {k: hex(v) for k, v in symbols.items()
                    if k in ("g_lcd_probe", "Reset_Handler", "_estack", "g_pfnVectors")},
        "gates": [d for d in defines if d.startswith("OTA_ENABLE_")],
    }
    (ROOT / "build" / "lcd-probe-build.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (ROOT / "build" / "lcd-probe-build.log").write_text("\n".join(logs), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gcc", required=True)
    ap.add_argument("--dfp", required=True)
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
