"""Build the NOR backup read-only firmware from the frozen core + reference BSP.

Binds BOTH the frozen core snapshot and this stage's own inputs (firmware, host,
build script, linker) so the linked firmware is provably matched to the source
that produced it. Uses explicit exceptions, never bare assert. On failure the
previous ready_for_review record is invalidated, not left behind.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CORE = Path(r"D:\Project\Can\ota-core")
ROOT = Path(__file__).resolve().parent


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def core_source_hashes():
    spec = importlib.util.spec_from_file_location("core_snapshot", CORE / "tools/source_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.source_hashes()


def check_core_frozen():
    hashes = core_source_hashes()
    report = json.loads((CORE / "evidence/verification.json").read_text(encoding="utf-8"))
    if report.get("status") != "passed" or report.get("validated_source_hashes") != hashes:
        raise ValueError("The frozen core must match its successful verification")
    return hashes


def stage_hashes():
    excluded = {"build", "evidence", "__pycache__"}
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(ROOT.rglob("*"))
            if p.is_file() and not excluded.intersection(p.relative_to(ROOT).parts)
            and p.suffix in {".c", ".h", ".py", ".ld", ".ps1"}}


def validate_image(elf, binary):
    raw, image = Path(elf).read_bytes(), Path(binary).read_bytes()
    if not (raw[:6] == b"\x7fELF\x01\x01" and len(raw) >= 52):
        raise ValueError("Expected ELF32 little endian")
    kind, machine = struct.unpack_from("<HH", raw, 16)
    entry, phoff = struct.unpack_from("<II", raw, 24)
    entsize, count = struct.unpack_from("<HH", raw, 42)
    if not (kind == 2 and machine == 40 and entsize == 32):
        raise ValueError("Expected executable ARM ELF")
    stack, reset = struct.unpack_from("<II", image)
    if not (stack == 0x20020000 and reset & 1 and 0x08000000 <= (reset & ~1) < 0x08010000):
        raise ValueError("Invalid boot reset vector")
    if entry != reset:
        raise ValueError("ELF entry and reset vector disagree")
    loaded, ram_end = [], 0x20000000
    for i in range(count):
        ptype, off, addr, phys, size, memsize, _, _ = struct.unpack_from("<8I", raw, phoff + i * entsize)
        if ptype != 1:
            continue
        if off + size > len(raw) or size > memsize:
            raise ValueError("Invalid ELF segment sizes")
        if size:
            if not (0x08000000 <= phys < 0x08010000 and size <= 0x08010000 - phys):
                raise ValueError("ELF load bytes escape the boot region")
            if raw[off:off + size] != image[phys - 0x08000000:phys - 0x08000000 + size]:
                raise ValueError("BIN does not match ELF load bytes")
            loaded.append((phys, phys + size))
        if 0x20000000 <= addr < 0x20020000:
            if memsize > 0x20020000 - addr:
                raise ValueError("ELF segment escapes main SRAM")
            ram_end = max(ram_end, addr + memsize)
        else:
            if not (0x08000000 <= addr < 0x08010000 and memsize <= 0x08010000 - addr):
                raise ValueError("Unexpected runtime segment address")
    if not loaded or min(x[0] for x in loaded) != 0x08000000:
        raise ValueError("Missing boot base segment")
    if max(x[1] for x in loaded) - 0x08000000 != len(image):
        raise ValueError("Unexpected BIN extent")
    return {"flash_bytes": len(image), "initial_sp": hex(stack), "reset_vector": hex(reset),
            "ram_bytes_with_reserve": ram_end - 0x20000000}


def build(args):
    gcc, dfp = Path(args.gcc).resolve(), Path(args.dfp).resolve()
    core_hashes = check_core_frozen()
    stage_snapshot = stage_hashes()
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    result_path = evidence / "nor-backup-build.json"
    try:
        _build_locked(gcc, dfp, core_hashes)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result_path.write_text(json.dumps({"status": "failed", "error": str(error),
                                           "target_linked": False, "ready_for_review": False}) + "\n",
                               encoding="utf-8")
        raise
    if stage_hashes() != stage_snapshot:
        result_path.write_text(json.dumps({"status": "failed",
                                           "error": "stage inputs changed during build",
                                           "target_linked": False, "ready_for_review": False}) + "\n",
                               encoding="utf-8")
        raise RuntimeError("stage inputs changed during the NOR firmware build")
    return 0


def _build_locked(gcc, dfp, core_hashes):
    project = CORE / "mdk/explorer_readonly.uvprojx"
    target = next(t for t in ET.parse(project).findall("Targets/Target")
                  if t.findtext("TargetName") == "explorer_ZG_loopback_READONLY")
    controls = target.find("TargetOption/TargetArmAds/Cads/VariousControls")
    defines = [d for d in controls.findtext("Define").split(",") if d != "__MICROLIB"]
    for flag in ("OTA_ENABLE_FLASHING", "OTA_ENABLE_BOOT_HANDOFF", "OTA_ENABLE_WATCHDOG"):
        if flag + "=0" not in defines:
            raise ValueError("all write/handoff/watchdog gates must stay off")
    includes = [(project.parent / p.replace("\\", "/")).resolve()
                for p in controls.findtext("IncludePath").split(";")]
    sources = [(project.parent / p.text.replace("\\", "/")).resolve()
               for p in target.findall("Groups/Group/Files/File/FilePath")]
    sources = [p for p in sources if p.suffix == ".c"]
    loopback_main = CORE / "firmware/explorer_loopback/main.c"
    if sources.count(loopback_main) != 1:
        raise ValueError("loopback main not found in plan")
    sources.remove(loopback_main)
    vendor_system = dfp / "Drivers/CMSIS/Device/ST/STM32F4xx/Source/Templates/system_stm32f4xx.c"
    startup = vendor_system.parent / "gcc/startup_stm32f407xx.s"
    if vendor_system not in sources or not startup.is_file():
        raise ValueError("missing ST system/startup")
    sources += [ROOT / "firmware/nor_backup_main.c", ROOT / "firmware/nor_stream.c",
                CORE / "firmware/runtime/newlib_init.c", startup]
    objcopy = gcc.with_name("arm-none-eabi-objcopy" + gcc.suffix)
    nm = gcc.with_name("arm-none-eabi-nm" + gcc.suffix)
    name = "nor_backup_READONLY"
    directory = ROOT / "build" / name
    directory.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    version = subprocess.run([gcc, "--version"], capture_output=True, text=True, check=True, timeout=15).stdout
    logs = []

    def execute(command, expected=0):
        run = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=120)
        text = run.stdout + run.stderr
        logs.append("COMMAND: " + " ".join(map(str, command)) + "\n" + text)
        if run.returncode != expected:
            raise RuntimeError(f"exit {run.returncode}: {text}")
        return text

    common = [gcc, "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-Os", "-g3",
              "-ffunction-sections", "-fdata-sections", "-fno-common", "-std=c99"]
    common += ["-D" + d for d in defines]
    for path in [ROOT / "firmware", CORE, *includes[:5]]:
        common += ["-I", str(path)]
    for path in includes[5:]:
        common += ["-isystem", str(path)]
    objects = []
    for i, source in enumerate(sources):
        output = directory / f"{i:02d}_{source.stem}.o"
        flags = ["-Wall", "-Wextra"] if source.suffix == ".c" else []
        if source.is_relative_to(CORE) or source.is_relative_to(ROOT):
            flags += ["-Werror"]
        execute(common + flags + ["-c", source, "-o", output])
        objects.append(output)
    elf, binary, mapfile = [directory / (name + s) for s in (".elf", ".bin", ".map")]
    execute([gcc, "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-nostartfiles",
             "--specs=nano.specs", "--specs=nosys.specs", "-T", ROOT / "firmware/nor_backup.ld",
             "-Wl,--gc-sections", "-Wl,--fatal-warnings", "-Wl,-Map=" + str(mapfile),
             *objects, "-o", elf])
    execute([objcopy, "-O", "binary", elf, binary])
    layout = validate_image(elf, binary)
    symbols = {}
    for line in execute([nm, "-n", "-S", elf]).splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch("[0-9a-fA-F]+", parts[0]):
            symbols[parts[-1]] = int(parts[0], 16)
    for sym in ("nor_mailbox", "Reset_Handler", "_estack", "g_pfnVectors"):
        if sym not in symbols:
            raise ValueError("missing symbol: " + sym)
    mailbox_addr = symbols["nor_mailbox"]
    if not (mailbox_addr % 4 == 0 and 0x20000000 <= mailbox_addr
            and mailbox_addr + 40 + 8192 <= 0x20020000):
        raise ValueError("mailbox escapes SRAM or is misaligned")
    if symbols["g_pfnVectors"] != 0x08000000 or symbols["_estack"] != 0x20020000:
        raise ValueError("vector/MSP symbols disagree")
    report = {
        "status": "ready_for_review",
        "target": name,
        "compiler": version.splitlines()[0],
        "compiler_sha256": digest(gcc),
        "core_hashes": core_hashes,
        "stage_input_sha256": stage_hashes(),
        "objects": len(objects),
        "linked": True,
        "hardware_executed": False,
        "layout": layout,
        "mailbox": {"symbol": "nor_mailbox", "address": hex(mailbox_addr),
                    "header_bytes": 40, "data_bytes": 8192, "struct_bytes": 40 + 8192},
        "artifacts": [
            {"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in (elf, binary, mapfile)
        ],
        "bin_sha256": digest(binary),
    }
    (evidence / "nor-backup-build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (evidence / "nor-backup-build.txt").write_text("\n".join(logs), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gcc", type=Path, required=True)
    parser.add_argument("--dfp", type=Path, required=True)
    args = parser.parse_args()
    try:
        raise SystemExit(build(args))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("BUILD FAILED:", error, file=sys.stderr)
        raise SystemExit(1)
