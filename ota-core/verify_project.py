"""One reproducible host/ARM-preparation check. Does not access CAN hardware."""
import argparse
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"tools"))
from make_image import APP,package,DEMO_SEED
from ed25519 import L
from source_snapshot import source_hashes

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cc",required=True)
    parser.add_argument("--arm-bin",type=Path)
    parser.add_argument("--dfp",type=Path)
    parser.add_argument("--cmsis",type=Path)
    parser.add_argument("--gcc",type=Path,help="Optional GNU Arm GCC executable for full read-only target links")
    parser.add_argument("--python-can-virtual",action="store_true")
    parser.add_argument("--python-can-path",type=Path)
    args=parser.parse_args()
    if args.gcc and not (args.dfp and args.cmsis):
        parser.error("--gcc requires --dfp and --cmsis")
    evidence=ROOT/"evidence";evidence.mkdir(exist_ok=True)
    build=ROOT/"build/verification";build.mkdir(parents=True,exist_ok=True)
    native=Path(args.cc).stem.lower()=="tcc"
    def run(command,log=None,expected=0):
        result=subprocess.run(list(map(str,command)),cwd=ROOT,capture_output=True,text=True,timeout=180)
        output=result.stdout+result.stderr
        if log:
            log.write_text("COMMAND: "+" ".join(map(str,command))+"\n"+output,encoding="utf-8")
        if result.returncode!=expected:
            print(output)
            raise RuntimeError(f"unexpected exit {result.returncode}: {command[0]}")
        return output
    started=time.monotonic()
    snapshot=source_hashes()
    (evidence/"verification.json").write_text(json.dumps(dict(status="running",
        hardware_verified=False,target_linked=False))+"\n",encoding="utf-8")
    previous=json.loads((evidence/"results.json").read_text(encoding="utf-8"))
    core=dict(baseline=previous["baseline"],hardware_verified=False,signature_implemented=True)
    for capacity in (1024,512):
        command=[sys.executable,"run_tests.py","--cc",args.cc,"--capacity",capacity]
        if native: command+=["--in-memory"]
        output=run(command,evidence/f"host-{capacity}KiB.txt")
        match=re.search(r"(\d+) checks, (\d+) failures",output)
        faults=re.search(r"(\d+) storage power-cut scenarios",output)
        if not match or not faults or int(match.group(2)): raise RuntimeError("missing/failed core checks")
        core[str(capacity)]=dict(checks=int(match.group(1)),failures=0,
            storage_power_cut_scenarios=int(faults.group(1)),
            execution="TinyCC native in-memory C" if native else "native C executable")
        print("Core",capacity,core[str(capacity)],flush=True)
    run([sys.executable,"run_pc_checks.py","--cc",args.cc],build/"pc.txt")
    run([sys.executable,"run_driver_checks.py","--cc",args.cc],build/"nor.txt")
    run([sys.executable,"run_firmware_checks.py","--cc",args.cc],build/"firmware.txt")
    virtual=dict(performed_this_run=False,usb_hardware_tested=False)
    if args.python_can_virtual:
        command=[sys.executable,"run_virtual_can_checks.py","--cc",args.cc]
        if args.python_can_path: command += ["--module-path",args.python_can_path]
        run(command,build/"python-can-virtual.txt")
        virtual=json.loads((evidence/"python-can-virtual-results.json").read_text(encoding="utf-8"))
    command=[sys.executable,"run_stm32_checks.py","--cc",args.cc]
    if native: command+=["--in-memory"]
    for name in ("arm_bin","dfp","cmsis"):
        if getattr(args,name): command += ["--"+name.replace("_","-"),getattr(args,name)]
    run(command,build/"stm32.txt")
    explorer=dict(performed_this_run=False,hardware_verified=False,target_linked=False)
    if args.arm_bin and args.dfp and args.cmsis:
        run([sys.executable,"run_explorer_checks.py","--arm-bin",args.arm_bin,
             "--dfp",args.dfp,"--cmsis",args.cmsis],build/"explorer.txt")
        explorer=json.loads((evidence/"explorer-results.json").read_text(encoding="utf-8"))
        explorer["performed_this_run"]=True
    print("PC, NOR and STM32 preparation passed",flush=True)
    gnu=dict(performed_this_run=False,target_linked=False,hardware_verified=False)
    if args.gcc:
        run([sys.executable,"tools/build_explorer_gnu.py","--gcc",args.gcc,
             "--dfp",args.dfp,"--cmsis",args.cmsis],build/"explorer-gnu.txt")
        gnu=json.loads((evidence/"explorer-gnu-results.json").read_text(encoding="utf-8"))
        if gnu.get("status")!="passed" or gnu.get("validated_source_hashes")!=snapshot:
            raise RuntimeError("GNU target build does not match the current source snapshot")
        gnu["performed_this_run"]=True
        print("Four complete GNU read-only images linked and validated",flush=True)
    if args.arm_bin:
        cc=args.arm_bin/("armclang.exe" if sys.platform=="win32" else "armclang")
        logs=[]
        for capacity in (1024,512):
            sources=[p for pattern in ("src/core/*.c","src/port/host/*.c","tests/*.c")
                     for p in sorted(ROOT.glob(pattern))]
            command=[cc,"--target=arm-arm-none-eabi","-mcpu=cortex-m4","-std=c99",
                     "-Wall","-Wextra","-Werror","-fsyntax-only","-Iinclude","-Isrc/port/host",
                     "-Ithird_party/ed25519",
                     f"-DFLASH_TOTAL_SIZE={capacity*1024}u",*sources]
            output=run(command)
            logs.append("COMMAND: "+" ".join(map(str,command))+"\nEXIT: 0\n"+output)
        (evidence/"arm-syntax.txt").write_text("\n".join(logs),encoding="utf-8")
        core["cortex_m4_syntax"]=dict(capacities_bytes=[1048576,524288],exit_codes=[0,0],
                                     flags=["-Wall","-Wextra","-Werror"],target_linked=False)
    else:
        core["cortex_m4_syntax"]=dict(performed_this_run=False,target_linked=False)
    output=run([sys.executable,"-m","unittest","discover","-s","tests_tools","-v"])
    count=re.search(r"Ran (\d+) tests? in",output)
    if not count or "skipped=" in output: raise RuntimeError("missing/skipped packaging checks")
    valid=package(struct.pack("<II",0x20020000,APP+9)+b"\xA5"*11,7,seed=DEMO_SEED)
    bad_header=bytearray(valid);bad_header[8]^=1
    bad_payload=bytearray(valid);bad_payload[32]^=1
    bad_signature=bytearray(valid);bad_signature[-1]^=1
    noncanonical=bytearray(valid)
    noncanonical[-32:]=(int.from_bytes(valid[-32:],"little")+L).to_bytes(32,"little")
    unsigned=package(struct.pack("<II",0x20020000,APP+9)+b"\xA5"*11,7)
    wrong_key=package(struct.pack("<II",0x20020000,APP+9)+b"\xA5"*11,7,
                      seed=bytes(range(1,33)))
    command=[args.cc,"-std=c99","-Wall","-Wextra","-Iinclude","-Ithird_party/ed25519",
             "src/core/image.c","src/core/crc32.c","src/core/signature.c"]
    command += [str(p.relative_to(ROOT)) for p in sorted((ROOT/"third_party/ed25519").glob("*.c"))]
    if native:
        validator=command+["-run","tests_tools/check_package.c"]
    else:
        binary=build/("check-package.exe" if sys.platform=="win32" else "check-package")
        run(command+["tests_tools/check_package.c","-o",binary])
        validator=[binary]
    cases=(("valid",valid,0),("bad-header",bad_header,1),("bad-payload",bad_payload,1),
           ("bad-signature",bad_signature,1),("noncanonical-scalar",noncanonical,1),
           ("unsigned",unsigned,1),("wrong-key",wrong_key,1),("truncated-signature",valid[:-1],1))
    for name,data,expected in cases:
        path=build/(name+".img");path.write_bytes(data)
        output+=f"\n{name}: exit={expected}\n"+run(validator+[path],expected=expected)
    (evidence/"image-packaging.txt").write_text(output,encoding="utf-8")
    (evidence/"image-packaging-results.json").write_text(json.dumps(dict(python_unittest=int(count.group(1)),
        cross_language={name:"accepted" if expected==0 else "rejected" for name,_,expected in cases},
        hardware_verified=False),indent=2)+"\n",encoding="utf-8")
    run([sys.executable,"tools/demo_roundtrip.py","--cc",args.cc],build/"roundtrip.txt")
    run([sys.executable,"tools/demo_recovery.py","--cc",args.cc],build/"recovery.txt")
    (evidence/"results.json").write_text(json.dumps(core,indent=2)+"\n",encoding="utf-8")
    if source_hashes()!=snapshot: raise RuntimeError("source changed during verification; rerun")
    report=dict(status="passed",duration_s=round(time.monotonic()-started,3),
                validated_source_hashes=snapshot,
                hardware_verified=False,target_linked=bool(gnu.get("target_linked")),
                keil_target_linked=False,signature_implemented=True,
                core=core,pc=json.loads((evidence/"pc-results.json").read_text(encoding="utf-8")),
                nor=json.loads((evidence/"nor-results.json").read_text(encoding="utf-8")),
                firmware=json.loads((evidence/"firmware-results.json").read_text(encoding="utf-8")),
                python_can_virtual=virtual,
                stm32=json.loads((evidence/"stm32-results.json").read_text(encoding="utf-8")),
                explorer=explorer,
                gnu_readonly_targets=gnu,
                image_packaging_python_tests=int(count.group(1)))
    (evidence/"verification.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("All requested checks passed; evidence/verification.json",flush=True)
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
        (ROOT/"evidence/verification.json").write_text(json.dumps(dict(status="failed",error=str(error)))+"\n",encoding="utf-8")
        print(f"Verification failed: {error}",file=sys.stderr)
        raise SystemExit(1)
