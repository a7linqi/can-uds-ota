"""Pre-flight check for moving this project to another machine.

The project is not self-contained: the toolchains live outside git (1.5 GB)
and so does the golden NOR dump (16 MiB, third-party content). Two hard-coded
absolute paths are also baked into 72 files across the tree, so the layout on
the new machine has to match the old one.

    python stages/board-lab/tools/check_environment.py
    python stages/board-lab/tools/check_environment.py --skip-hardware
    python stages/board-lab/tools/check_environment.py --root E:\\Project\\Can

Exit code 0 = everything the next hardware step needs is present.
1 = at least one required piece is missing (each one prints how to fix it).
"""

import argparse
import hashlib
import subprocess
import sys
import zlib
from pathlib import Path

ROOT_DEFAULT = Path(r"D:\Project\Can")
LEARNING_DEFAULT = Path(r"D:\Project\Can-learning")
CLI_DEFAULT = Path(r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe")

GOLDEN_REL = Path(
    "backups/nor-original/full/20261103_200408_779736/pass1/pass1.bin")
GOLDEN_SHA = "929af96ac660590418cb74878033b742bcd31d9e7108933ce769548f40e305e1"
GOLDEN_CRC = 0xA4188821

# USB-to-serial bridges that can actually drive the bench. Anything else
# (Bluetooth PAN, motherboard COM1) enumerates fine but has no wires on PA9/PA10.
USB_SERIAL_IDS = {
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x5523): "CH341",
    (0x1A86, 0x55D3): "CH343",
    (0x10C4, 0xEA60): "CP2102",
    (0x0403, 0x6001): "FT232",
}

ok_count = 0
missing = []
warned = []


def line(tag, name, detail=""):
    print(f"[{tag:4s}] {name:26s} {detail}")


def ok(name, detail=""):
    global ok_count
    ok_count += 1
    line("OK", name, detail)


def miss(name, fix):
    missing.append((name, fix))
    line("MISS", name, fix)


def warn(name, detail):
    warned.append((name, detail))
    line("WARN", name, detail)


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def crc32_of(path, chunk=1 << 20):
    crc = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            crc = zlib.crc32(block, crc)
    return crc & 0xFFFFFFFF


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--learning", default=str(LEARNING_DEFAULT))
    ap.add_argument("--cli", default=str(CLI_DEFAULT))
    ap.add_argument("--skip-hardware", action="store_true",
                    help="do not probe ST-Link / serial ports")
    args = ap.parse_args()

    root = Path(args.root)
    learning = Path(args.learning)
    cli = Path(args.cli)

    print("Environment check for the CAN/UDS OTA bench")
    print(f"python {sys.version.split()[0]}  at  {sys.executable}")

    # ---------------------------------------------------------------- layout
    section("1. Project layout (must match the old machine byte for byte)")
    for name, path in (("workspace", root), ("learning copy", learning)):
        if path.is_dir():
            ok(name, str(path))
        else:
            miss(name, f"expected at {path}")
    if root.is_dir() and root.resolve() != ROOT_DEFAULT:
        warn("root moved", f"{root} != {ROOT_DEFAULT}; "
                           f"72 files still hard-code the old path")
    if learning.is_dir() and learning.resolve() != LEARNING_DEFAULT:
        warn("learning moved", f"{learning} != {LEARNING_DEFAULT}; "
                               f"tools/ and build-temp/ still point at the old one")

    # ------------------------------------------------------------- toolchain
    section("2. Toolchains (not in git: 1.5 GB)")
    tcc = root / "toolchain/tccbox-2025.10.27.data/purelib/tccbox/tcc_dist/tcc.exe"
    if tcc.is_file():
        ok("TinyCC", str(tcc))
    else:
        miss("TinyCC", "copy the whole toolchain/ directory from the old machine")

    gcc = root / ("toolchain/xpack-arm-none-eabi-gcc-15.2.1-1.1"
                  "/bin/arm-none-eabi-gcc.exe")
    if gcc.is_file():
        ok("GNU Arm GCC", str(gcc))
    else:
        miss("GNU Arm GCC", "copy toolchain/ from the old machine "
                            "(or install xpack-arm-none-eabi-gcc 15.2.1)")

    if cli.is_file():
        ok("CubeProgrammer CLI", str(cli))
    else:
        miss("CubeProgrammer CLI", f"install at {cli.parent.parent}, "
                                   f"or pass --cli <real path>")

    # Keil is deliberately not required: armlink fails on this machine with a
    # DLL relocation error, every image is linked with GNU ld instead.
    keil = root / ".." / "Keil"
    line("--", "Keil / armlink", "not required (GNU ld is what actually links)")

    # ---------------------------------------------------------------- python
    section("3. Python packages")
    if sys.version_info >= (3, 10):
        ok("python >= 3.10", sys.version.split()[0])
    else:
        miss("python >= 3.10", "install Python 3.12")

    for module, pipname in (("serial", "pyserial"), ("can", "python-can")):
        try:
            mod = __import__(module)
            ver = getattr(mod, "__version__", "?")
            ok(pipname, f"{ver}")
        except ImportError:
            miss(pipname, f"pip install {pipname}")

    # ------------------------------------------------------------------ data
    section("4. Data that git does not carry")
    golden = root / GOLDEN_REL
    if golden.is_file():
        size = golden.stat().st_size
        if size != 16 * 1024 * 1024:
            miss("golden NOR dump", f"size {size} != 16 MiB")
        else:
            got = sha256_of(golden)
            if got == GOLDEN_SHA:
                ok("golden NOR dump", f"16 MiB, sha256 {got[:12]}...")
            else:
                warn("golden sha256", f"{got[:12]}... != {GOLDEN_SHA[:12]}...")
            crc = crc32_of(golden)
            if crc == GOLDEN_CRC:
                ok("golden CRC32", f"{crc:#010x}")
            else:
                miss("golden CRC32", f"{crc:#010x} != {GOLDEN_CRC:#010x}, "
                                     f"this is NOT the image that was verified")
    else:
        miss("golden NOR dump", "copy backups/ from the old machine (177 MB)")

    lab = root / "stages/board-lab"
    images = [
        ("restore image (write)", lab / "build/nor_restore_WRITE/nor_restore_WRITE.bin"),
        ("backup image (read)", lab / "build/nor_backup_READONLY/nor_backup_READONLY.bin"),
        ("UART bench image", learning / "ota-core/build/gnu-explorer/"
                             "explorer_ZG_uart_READONLY/explorer_ZG_uart_READONLY.bin"),
    ]
    for name, path in images:
        if path.is_file():
            ok(name, f"{path.stat().st_size} B")
        else:
            warn(name, f"missing at {path}; rebuild with build_restore.py / verify_project.py")

    # -------------------------------------------------------------- hardware
    if args.skip_hardware:
        section("5. Hardware (skipped)")
        line("--", "--skip-hardware", "not probing ST-Link or serial ports")
    else:
        section("5. Hardware")
        if cli.is_file():
            try:
                out = subprocess.run([str(cli), "-l"], capture_output=True,
                                     text=True, timeout=30).stdout
                if "ST-Link" in out or "ST-LINK" in out:
                    ok("ST-Link", "detected by CubeProgrammer")
                else:
                    miss("ST-Link", "plug the probe in; check Device Manager")
            except (subprocess.TimeoutExpired, OSError) as error:
                warn("ST-Link probe", f"CLI failed: {error}")
        else:
            line("--", "ST-Link", "no CLI to probe with")

        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
            if not ports:
                warn("serial port", "none at all; the CH340 driver is probably missing")
            for p in ports:
                kind = USB_SERIAL_IDS.get((p.vid, p.pid))
                if kind:
                    ok("USB serial", f"{p.device}  {kind}  {p.description}")
                else:
                    line("--", p.device, p.description or "(no description)")
            if not any((p.vid, p.pid) in USB_SERIAL_IDS for p in ports):
                warn("USB serial adapter",
                     "no CH340/CP2102/FT232 found -- Bluetooth and motherboard "
                     "ports cannot drive the UART bench")
        except Exception as error:
            warn("serial port", f"cannot enumerate: {error}")

    # ------------------------------------------------------------------- git
    section("6. Repository state")
    for name, path in (("workspace", root), ("learning copy", learning)):
        if not (path / ".git").is_dir():
            warn(name, "not a git repository")
            continue
        try:
            branch = subprocess.run(["git", "-C", str(path), "rev-parse",
                                     "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True, timeout=15)
            dirty = subprocess.run(["git", "-C", str(path), "status",
                                    "--porcelain"],
                                   capture_output=True, text=True, timeout=15)
            count = len([x for x in dirty.stdout.splitlines() if x.strip()])
            ok(name, f"branch {branch.stdout.strip()}, {count} uncommitted")
        except (subprocess.TimeoutExpired, OSError) as error:
            warn(name, f"git failed: {error}")

    # -------------------------------------------------------------- summary
    print()
    print("=" * 74)
    print(f"Summary: {ok_count} ok, {len(missing)} missing, {len(warned)} warning(s)")
    if missing:
        print()
        print("Blocking items -- fix these before touching the board:")
        for name, fix in missing:
            print(f"  - {name}: {fix}")
        print()
        print("If the project moved to another drive letter, either give that "
              "machine a D: drive\nor run:  subst D: <new root's parent>")
        return 1
    print()
    print("Everything the next hardware step needs is present.")
    print("Next: python build-temp/drill_accept.py   (what the last run left behind)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
