"""Package board-lab Phase-3 deliverables into a uniquely-named ZIP.

Only this stage's own directory is packaged; the frozen core and app-bringup
trees are never touched, and the core packager is never invoked. The output
name embeds a timestamp so a historical package is never overwritten; creation
is exclusive. The linked firmware artifact is cross-checked against the build
record before packaging.
"""
import hashlib
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def include(path):
    rel = path.relative_to(ROOT).as_posix()
    parts = rel.split("/")
    if "__pycache__" in parts or rel.endswith((".pyc", ".o")):
        return False
    if rel == "evidence/delivery-index.json":
        return False  # written explicitly at the end
    for token in ("selftest", "short-", "empty", "over-300", "over-257", "nor-sim"):
        if token in rel:
            return False
    if parts[0] == "build" and parts[1] == "nor_backup_READONLY":
        return path.suffix in {".elf", ".bin", ".map"}
    if rel == "build/eeprom_probe.exe":
        return True
    if parts[0] == "build":
        return False
    return True


def main():
    from build_nor import stage_hashes, check_core_frozen
    # Cross-check the linked firmware against the build record.
    record_path = ROOT / "evidence" / "nor-backup-build.json"
    if not record_path.is_file():
        print("ERROR: run build_nor.py first (no build record)", file=sys.stderr)
        return 1
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("status") != "ready_for_review":
        print("ERROR: firmware is not ready_for_review", file=sys.stderr)
        return 1
    checks_path = ROOT / "evidence/review-checks.json"
    if not checks_path.is_file():
        raise ValueError("Run tests/run_checks.py before packaging")
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    current = stage_hashes()
    if record.get("stage_input_sha256") != current or checks.get("stage_input_sha256") != current or checks.get("status") != "passed":
        raise ValueError("Stage evidence is stale")
    if record.get("core_hashes") != check_core_frozen():
        raise ValueError("Core binding changed")
    for item in record["artifacts"]:
        p = ROOT / item["path"]
        if p.stat().st_size != item["bytes"] or hashlib.sha256(p.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("ELF/BIN/MAP mismatch")
    bin_path = ROOT / "build/nor_backup_READONLY/nor_backup_READONLY.bin"
    if not bin_path.is_file():
        print("ERROR: linked BIN missing", file=sys.stderr)
        return 1
    bin_sha = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    if bin_sha != record.get("bin_sha256"):
        print("ERROR: linked BIN does not match the build record", file=sys.stderr)
        return 1

    files = [p for p in sorted(ROOT.rglob("*")) if p.is_file() and include(p)]
    index = {}
    for p in files:
        index[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    index_path = ROOT / "evidence" / "delivery-index.json"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT.parents[1] / "deliverables" / f"board-lab-phase3-r1-{stamp}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        print(f"ERROR: package already exists: {out}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, "board-lab/" + p.relative_to(ROOT).as_posix())
        z.write(index_path, "board-lab/evidence/delivery-index.json")
    with zipfile.ZipFile(out) as z:
        if z.testzip() is not None:
            raise ValueError("zip integrity check failed")
        for name, digest in index.items():
            if hashlib.sha256(z.read("board-lab/" + name)).hexdigest() != digest:
                raise ValueError("zip content hash mismatch")
    print(json.dumps({"file": str(out), "bytes": out.stat().st_size,
                      "files": len(files) + 1, "firmware_bin_sha256": bin_sha,
                      "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
