"""Run command-level NOR tests; a chip fixture is not physical SPI evidence."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cc",required=True)
    args=parser.parse_args()
    command=[args.cc,"-std=c99","-Wall","-Wextra","-Iinclude","-Isrc/port/host",
             "-Ithird_party/ed25519"]
    command += [str(p.relative_to(ROOT)) for pattern in ("src/core/*.c","src/port/host/*.c","src/driver/*.c","third_party/ed25519/*.c")
                for p in sorted(ROOT.glob(pattern))]
    binary=ROOT/"build"/("nor-tests.exe" if sys.platform=="win32" else "nor-tests")
    binary.parent.mkdir(exist_ok=True)
    native=Path(args.cc).stem.lower()=="tcc"
    command+=(["-run","tests_driver/test_spi_nor.c"] if native else
              ["tests_driver/test_spi_nor.c","-o",str(binary)])
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=60)
    output=run.stdout+run.stderr
    code=run.returncode
    if not code and not native:
        run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=60)
        output+=run.stdout+run.stderr;code=run.returncode
    evidence=ROOT/"evidence"; evidence.mkdir(exist_ok=True)
    (evidence/"nor-tests.txt").write_text("COMMAND: "+" ".join(command)+"\n"+output,encoding="utf-8")
    print(output,end="")
    result=re.search(r"SPI NOR protocol: (\d+) checks, (\d+) failures",output)
    if code or not result or int(result.group(2)): return code or 1
    report=dict(checks=int(result.group(1)),failures=0,chip_profile="W25Q128JV-IQ/JQ EF4018",
                evidence="SPI command fixture",hardware_verified=False)
    points=re.search(r"SPI transfer failure points: (\d+)",output)
    if points: report["transfer_failure_points"]=int(points.group(1))
    (evidence/"nor-results.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
