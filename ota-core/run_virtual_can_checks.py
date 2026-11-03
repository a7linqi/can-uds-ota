"""Optional real python-can library check; only the in-memory virtual interface."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cc",required=True)
    parser.add_argument("--module-path",type=Path,help="optional isolated pip --target directory")
    args=parser.parse_args()
    env=os.environ.copy();env["OTA_TEST_CC"]=args.cc
    if args.module_path:
        env["PYTHONPATH"]=str(args.module_path.resolve())+os.pathsep+env.get("PYTHONPATH","")
    version=subprocess.run([sys.executable,"-c","import can; print(can.__version__)"],
                            env=env,capture_output=True,text=True)
    if version.returncode:
        print("Install optional python-can==4.6.1 or provide --module-path",file=sys.stderr);return 1
    command=[sys.executable,"-m","unittest","discover","-s","tests_virtual_can","-v"]
    result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    output=result.stdout+result.stderr
    evidence=ROOT/"evidence";evidence.mkdir(exist_ok=True)
    (evidence/"python-can-virtual.txt").write_text("python-can="+version.stdout.strip()+"\n"+output,encoding="utf-8")
    print(output,end="")
    count=re.search(r"Ran (\d+) tests? in",output)
    if result.returncode or not count or "skipped=" in output: return result.returncode or 1
    report=dict(tests=int(count.group(1)),failures=0,python_can_version=version.stdout.strip(),
                interface="virtual",ecu="actual C child process",usb_hardware_tested=False,hardware_verified=False)
    (evidence/"python-can-virtual-results.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
