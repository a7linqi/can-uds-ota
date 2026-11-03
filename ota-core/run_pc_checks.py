"""Run PC-to-C integration and client/driver-contract checks; reject skipped tests."""
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
    parser.add_argument("--cc",required=True,help="working host C99 compiler")
    args=parser.parse_args()
    environment=os.environ.copy()
    environment["OTA_TEST_CC"]=args.cc
    command=[sys.executable,"-m","unittest","discover","-s","tests_pc","-v"]
    run=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=120)
    output=run.stdout+run.stderr
    evidence=ROOT/"evidence"
    evidence.mkdir(exist_ok=True)
    (evidence/"pc-tests.txt").write_text("COMMAND: "+" ".join(command)+
        "\nOTA_TEST_CC="+args.cc+"\n"+output,encoding="utf-8")
    print(output,end="")
    count=re.search(r"Ran (\d+) tests? in",output)
    if run.returncode or not count or "skipped=" in output or "... skipped" in output:
        return run.returncode or 1
    result=dict(tests=int(count.group(1)),failures=0,skipped=0,
                ecu="native compiled C core in a child process",
                transport="line-based virtual classic CAN",
                real_can_tested=False,hardware_verified=False)
    (evidence/"pc-results.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
