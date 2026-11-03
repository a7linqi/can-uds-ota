"""Compile, assemble and link four read-only Explorer images using GNU Arm GCC.

Reuses the generated Keil source/define plan and installed ST/CMSIS sources.
Only the startup syntax, linker script and C runtime hooks differ. No probe,
serial port or CAN interface is opened.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from source_snapshot import source_hashes

ROOT = Path(__file__).resolve().parents[1]
BOOT = 0x08000000
LIMIT = 0x08010000
RAM = 0x20000000
STACK = 0x20020000


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_image(elf, binary):
    raw = elf.read_bytes(); image = binary.read_bytes()
    assert raw[:6] == b"\x7fELF\x01\x01", "Expected ELF32 little endian"
    kind, machine = struct.unpack_from("<HH", raw, 16)
    entry, phoff = struct.unpack_from("<II", raw, 24)
    entsize, count = struct.unpack_from("<HH", raw, 42)
    assert kind == 2 and machine == 40 and entsize == 32
    assert 8 <= len(image) <= LIMIT - BOOT
    stack, reset = struct.unpack_from("<II", image)
    assert stack == STACK and reset & 1 and BOOT + 8 <= (reset & ~1) < BOOT + len(image)
    assert entry == reset, "ELF entry must match the reset vector"
    loaded, memory = [], []
    for i in range(count):
        section = struct.unpack_from("<8I", raw, phoff + i * entsize)
        ptype, offset, address, physical, size, memsize, flags, align = section
        if ptype != 1:
            continue
        assert offset + size <= len(raw) and size <= memsize
        if size:
            assert BOOT <= physical < LIMIT and size <= LIMIT - physical
            assert raw[offset:offset + size] == image[physical - BOOT:physical - BOOT + size]
            loaded.append((physical, physical + size))
        if RAM <= address < STACK:
            assert memsize <= STACK - address
            memory.append((address, address + memsize))
        else:
            assert BOOT <= address < LIMIT and memsize <= LIMIT - address
    assert loaded and min(x[0] for x in loaded) == BOOT
    flash_end = max(x[1] for x in loaded)
    assert flash_end - BOOT == len(image)
    return dict(flash_base=hex(BOOT), flash_bytes=len(image), flash_limit=hex(LIMIT),
                ram_bytes_with_reserved_stack=max((x[1] for x in memory), default=RAM) - RAM,
                initial_sp=hex(stack), reset_vector=hex(reset), elf_entry=hex(entry))


def build(gcc, dfp, cmsis):
    gcc, dfp, cmsis = gcc.resolve(), dfp.resolve(), cmsis.resolve()
    build_root = ROOT / "build/gnu-explorer"; build_root.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / "evidence"; evidence.mkdir(exist_ok=True)
    snapshot = source_hashes()
    (evidence / "explorer-gnu-results.json").write_text(json.dumps(dict(
        status="running",target_linked=False,hardware_verified=False)) + "\n",encoding="utf-8")
    startup = dfp / "Drivers/CMSIS/Device/ST/STM32F4xx/Source/Templates/gcc/startup_stm32f407xx.s"
    objcopy = gcc.with_name("arm-none-eabi-objcopy" + gcc.suffix)
    size_tool = gcc.with_name("arm-none-eabi-size" + gcc.suffix)
    for path in (gcc, objcopy, size_tool, startup, cmsis / "core_cm4.h"):
        if not path.is_file():
            raise ValueError(f"Required compiler/pack file is missing: {path}")
    version = subprocess.run([gcc, "--version"], capture_output=True, text=True, check=True, timeout=15).stdout
    project = ROOT / "mdk/explorer_readonly.uvprojx"
    targets = ET.parse(project).findall("Targets/Target")
    assert len(targets) == 4
    reports, artifacts, vendor_paths = [], [], {startup}
    for target in targets:
        name = target.findtext("TargetName")
        directory = build_root / name; directory.mkdir(exist_ok=True)
        controls = target.find("TargetOption/TargetArmAds/Cads/VariousControls")
        defines = [d for d in controls.findtext("Define").split(",") if d != "__MICROLIB"]
        for flag in ("OTA_ENABLE_FLASHING", "OTA_ENABLE_BOOT_HANDOFF", "OTA_ENABLE_WATCHDOG"):
            assert flag + "=0" in defines
        includes = [(project.parent / p.replace("\\", "/")).resolve()
                    for p in controls.findtext("IncludePath").split(";")]
        assert (dfp / "Drivers/STM32F4xx_HAL_Driver/Inc").resolve() in includes
        assert cmsis in includes, "Regenerate the Keil source plan for this CMSIS location"
        files = [(project.parent / f.text.replace("\\", "/")).resolve()
                 for f in target.findall("Groups/Group/Files/File/FilePath")]
        sources = [p for p in files if p.suffix == ".c"]
        sources += [ROOT / "firmware/runtime/newlib_init.c", startup]
        vendor_paths.update(p for p in sources if not p.is_relative_to(ROOT))
        common = [str(gcc), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-Os", "-g3",
                  "-ffunction-sections", "-fdata-sections", "-fno-common", "-std=c99"]
        common += ["-D" + d for d in defines]
        for path in includes[:4]:
            common += ["-I", str(path)]
        for path in includes[4:]:
            common += ["-isystem", str(path)]
        logs, objects = [], []

        def execute(command):
            run = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True, timeout=60)
            log = "COMMAND: " + " ".join(map(str, command)) + "\n" + run.stdout + run.stderr
            logs.append(log)
            if run.returncode:
                (evidence / "explorer-gnu-failure.txt").write_text("\n".join(logs), encoding="utf-8")
                raise RuntimeError(log)
            return run.stdout + run.stderr

        for i, source in enumerate(sources):
            output = directory / f"{i:02d}_{source.stem}.o"
            flags = ["-Wall", "-Wextra"] if source.suffix == ".c" else []
            if source.is_relative_to(ROOT):
                flags += ["-Werror"]
            execute(common + flags + ["-c", source, "-o", output])
            objects.append(output)
        elf, binary, mapfile = [directory / (name + suffix) for suffix in (".elf", ".bin", ".map")]
        linker = ROOT / "firmware/linker/explorer_boot.ld"
        execute([gcc, "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-nostartfiles",
                 "--specs=nano.specs", "--specs=nosys.specs", "-T", linker,
                 "-Wl,--gc-sections", "-Wl,--fatal-warnings", "-Wl,-Map=" + str(mapfile),
                 *objects, "-o", elf])
        execute([objcopy, "-O", "binary", elf, binary])
        memory = execute([size_tool, elf])
        layout = validate_image(elf, binary)
        entry = dict(target=name, objects=len(objects), linked=True, image=layout,
                     hardware_verified=False, size_output=memory.strip())
        for path in (elf, binary, mapfile):
            artifacts.append(dict(target=name, path=path.relative_to(ROOT).as_posix(),
                                  bytes=path.stat().st_size, sha256=digest(path)))
        reports.append(entry)
        (evidence / (name + "-gnu.txt")).write_text("\n".join(logs), encoding="utf-8")
        print(entry, flush=True)
    # Bind installed vendor inputs as well as the repository snapshot.
    for path in includes[4:]:
        vendor_paths.update(path.rglob("*.h"))
    if source_hashes() != snapshot:
        raise RuntimeError("Source changed during GNU target build; rebuild after edits")
    report = dict(status="passed", compiler=version.splitlines()[0], compiler_sha256=digest(gcc),
                  targets=reports, artifacts=artifacts,
                  validated_source_hashes=snapshot,
                  vendor_input_sha256={str(p): digest(p) for p in sorted(vendor_paths)},
                  target_linked=True, hardware_verified=False, keil_target_linked=False)
    (evidence / "explorer-gnu-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    old_failure = evidence / "explorer-gnu-failure.txt"
    if old_failure.is_file():
        old_failure.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcc", type=Path, required=True)
    parser.add_argument("--dfp", type=Path, required=True)
    parser.add_argument("--cmsis", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.gcc, args.dfp, args.cmsis)
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        destination = ROOT / "evidence/explorer-gnu-results.json"
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(dict(status="failed",error=str(error),
            target_linked=False,hardware_verified=False),indent=2) + "\n",encoding="utf-8")
        print(f"GNU target build failed: {error}", file=sys.stderr)
        return 1
    print("Four complete GNU read-only target images linked and validated; no hardware was accessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
