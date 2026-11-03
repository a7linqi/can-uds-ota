"""Exercise real firmware App entry using a bounded clock/watchdog fixture."""
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
    evidence=ROOT/"evidence";evidence.mkdir(exist_ok=True)
    build=ROOT/"build/firmware-checks";build.mkdir(parents=True,exist_ok=True)
    sources=[p for pattern in ("src/core/*.c","src/driver/*.c","platform/stm32f407/src/*.c")
             for p in sorted(ROOT.glob(pattern)) if p.name not in ("stm32_arch.c","system_minimal.c")]
    sources+=sorted((ROOT/"third_party/ed25519").glob("*.c"))
    sources+=[ROOT/"tests_stm32/mock_board.c"]
    results=[]
    for capacity in (1024,512):
        command=[args.cc,"-std=c99","-Wall","-Wextra","-Iinclude","-Iplatform/stm32f407/include",
                 "-Itests_stm32","-Ithird_party/ed25519",f"-DFLASH_TOTAL_SIZE={capacity*1024}u","-DOTA_ENABLE_WATCHDOG=1",
                 "-DOTA_TEST_REAL_FIRMWARE=1",*[str(p.relative_to(ROOT)) for p in sources]]
        native=Path(args.cc).stem.lower()=="tcc"
        binary=build/(f"app-{capacity}"+(".exe" if sys.platform=="win32" else ""))
        command+=(["-run","tests_firmware/test_app.c"] if native else
                  ["tests_firmware/test_app.c","-o",str(binary)])
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
        output=run.stdout+run.stderr;code=run.returncode
        if not code and not native:
            run=subprocess.run([binary],capture_output=True,text=True,timeout=30)
            output+=run.stdout+run.stderr;code=run.returncode
        (evidence/f"firmware-app-{capacity}.txt").write_text("COMMAND: "+" ".join(command)+"\n"+output,encoding="utf-8")
        print(output,end="")
        match=re.search(r"App entry watchdog model: (\d+) checks, (\d+) failures",output)
        if code or not match or int(match.group(2)): return code or 1
        results.append(dict(flash_kib=capacity,checks=int(match.group(1)),failures=0,scenarios=8))
    command=[args.cc,"-std=c99","-Wall","-Wextra","-Iinclude","-Iplatform/stm32f407/include",
             "-DOTA_EXPLORER_CAN_LOOPBACK=1","platform/stm32f407/src/stm32_can.c"]
    binary=build/("explorer-loopback"+(".exe" if sys.platform=="win32" else ""))
    command+=(["-run","tests_firmware/test_explorer_loopback.c"] if native else
              ["tests_firmware/test_explorer_loopback.c","-o",str(binary)])
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
    output=run.stdout+run.stderr;code=run.returncode
    if not code and not native:
        run=subprocess.run([binary],capture_output=True,text=True,timeout=30)
        output+=run.stdout+run.stderr;code=run.returncode
    (evidence/"firmware-explorer-loopback.txt").write_text("COMMAND: "+" ".join(command)+"\n"+output,encoding="utf-8")
    print(output,end="")
    match=re.search(r"Explorer loopback entry model: (\d+) checks, (\d+) failures; (\d+) scenarios",output)
    if code or not match or int(match.group(2)): return code or 1
    explorer=dict(checks=int(match.group(1)),failures=0,scenarios=int(match.group(3)),
                  uses_real_entry_and_can_queue=True,physical_can_tested=False)
    (evidence/"firmware-results.json").write_text(json.dumps(dict(app_entry=results,
        explorer_loopback_entry=explorer,
        watchdog="virtual timeout, not physical IWDG",hardware_verified=False),indent=2)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
