"""Create a source/evidence archive; exclude generated binaries and caches."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile
from source_snapshot import source_hashes
ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={"build","__pycache__",".git",".pytest_cache","runs"}
def selected():
    return sorted(p for p in ROOT.rglob("*") if p.is_file()
                  and not EXCLUDE.intersection(p.relative_to(ROOT).parts)
                  and p.suffix not in {".pyc",".o",".exe",".axf",".map",".bin",".img"})
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=ROOT.parent/"deliverables/ota-core-L1.zip")
    parser.add_argument("--include-firmware",action="store_true",
                        help="Include source-matched, fully linked GNU read-only images")
    args=parser.parse_args()
    output=args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("archive must be outside the source tree")
    verification=ROOT/"evidence/verification.json"
    if not verification.is_file(): parser.error("run verify_project.py before packaging")
    report=json.loads(verification.read_text(encoding="utf-8"))
    if report.get("status")!="passed" or report.get("validated_source_hashes")!=source_hashes():
        parser.error("current source differs from successful verification; run verify_project.py")
    manifest_path=ROOT/"evidence/source-manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    paths={p.relative_to(ROOT).as_posix():p for p in selected() if p!=manifest_path}
    if args.include_firmware:
        build=report.get("gnu_readonly_targets",{})
        if not build.get("performed_this_run") or build.get("status")!="passed" or not build.get("target_linked"):
            parser.error("full GNU target build was not performed in the successful verification")
        if build.get("validated_source_hashes")!=source_hashes():
            parser.error("linked firmware source hashes are stale; run verification again")
        artifacts=build.get("artifacts",[])
        if len(artifacts)!=12: parser.error("expected ELF/BIN/MAP for four read-only targets")
        for item in artifacts:
            path=(ROOT/item["path"]).resolve()
            if not path.is_relative_to(ROOT/"build/gnu-explorer") or path.suffix not in {".elf",".bin",".map"}:
                parser.error("unexpected firmware artifact path")
            if not path.is_file() or path.stat().st_size!=item["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
                parser.error("linked firmware bytes differ from verified artifact")
            target=item["target"]
            if target not in {f"explorer_{p}_{r}_READONLY" for p in ("ZG","ZE") for r in ("loopback","diagnostics")}:
                parser.error("unexpected firmware target")
            paths[f"firmware-builds/{target}/{path.name}"]=path
    manifest={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in sorted(paths.items())}
    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for name,path in sorted(paths.items()):
            archive.write(path,"ota-core/"+name)
        archive.write(manifest_path,"ota-core/evidence/source-manifest.json")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,digest in manifest.items():
            assert hashlib.sha256(archive.read("ota-core/"+name)).hexdigest()==digest
    print(json.dumps({"file":str(output),"bytes":output.stat().st_size,
                      "files":len(manifest)+1,"sha256":hashlib.sha256(output.read_bytes()).hexdigest()}))
if __name__=="__main__":
    main()
