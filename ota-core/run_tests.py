"""Compile and run the native C regression suite; no Python protocol model."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--cc", help="C compiler executable (gcc, clang, tcc, or zig)")
parser.add_argument("--capacity", type=int, choices=(512, 1024), default=1024)
parser.add_argument("--in-memory", action="store_true", help="Execute C main in TinyCC without launching a generated EXE")
args = parser.parse_args()
compiler = args.cc or os.environ.get("CC") or next(
    (p for name in ("cc", "gcc", "clang", "tcc") if (p := shutil.which(name))), None
)
if not compiler:
    parser.error("No C compiler found; pass --cc with its executable path.")
outdir = root / "build" / str(args.capacity)
outdir.mkdir(parents=True, exist_ok=True)
binary = outdir / ("ota_tests.exe" if os.name == "nt" else "ota_tests")
command = [compiler]
if Path(compiler).stem.lower() == "zig":
    command.append("cc")
command += ["-std=c99", "-Wall", "-Wextra", "-Iinclude", "-Isrc/port/host",
            "-Ithird_party/ed25519"]
command += [f"-DFLASH_TOTAL_SIZE={args.capacity * 1024}u"]
command += [str(p.relative_to(root)) for pattern in
            ("src/core/*.c", "src/port/host/*.c", "tests/*.c", "third_party/ed25519/*.c")
            for p in sorted(root.glob(pattern))]
if args.in_memory:
    if Path(compiler).stem.lower() != "tcc":
        parser.error("--in-memory requires TinyCC")
    command.remove("tests\\test_main.c" if os.name == "nt" else "tests/test_main.c")
    command += ["-run", "tests/test_main.c"]
else:
    command += ["-o", str(binary)]
print("Compile:", " ".join(command), flush=True)
result = subprocess.run(command, cwd=root)
if result.returncode or args.in_memory:
    sys.exit(result.returncode)
sys.exit(subprocess.run([str(binary)], cwd=root).returncode)
