"""Hash implementation/build inputs, excluding generated products and logs."""
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={"build","__pycache__",".git",".pytest_cache","runs"}
SUFFIXES={".c",".h",".py",".s",".sct",".ld",".uvprojx",".xml"}

def source_hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ROOT.rglob("*")) if p.is_file()
            and not EXCLUDED.intersection(p.relative_to(ROOT).parts)
            and (p.suffix in SUFFIXES or p.name=="Makefile" or p.name.startswith("requirements"))}
