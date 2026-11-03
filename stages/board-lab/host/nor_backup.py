"""Host driver for the NOR full-image read-only backup over the SWD RAM mailbox.

Protocol v2. Default mode simulates the firmware state machine in-process (no
probe access); --hardware drives the real ST-Link. Two independent full passes
are read and must match byte-for-byte (SHA256). Every run uses a fresh, newly
created directory; a success manifest is published only after both passes are
verified, and a failure never touches a previous run's files.
"""
import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

NOR_TOTAL_SIZE = 0x01000000  # 16 MiB
NOR_BLOCK_SIZE = 8192
MAILBOX_MAGIC = 0x4E4F5244  # "NORD"
MAILBOX_VERSION = 2
HDR = "<10I"
HEADER_BYTES = 40
STRUCT_BYTES = HEADER_BYTES + NOR_BLOCK_SIZE  # 8232
SESSION_OFF = 8
ACK_OFF = 36
SRAM_BASE = 0x20000000
SRAM_END = 0x20020000

MB_IDLE, MB_BUSY, MB_READY, MB_DONE, MB_ERROR = 0, 1, 2, 3, 0xE0
ERR_NAME = {0: "NONE", 1: "INIT", 2: "JEDEC", 3: "SPI", 4: "ACK_TIMEOUT", 5: "BAD_SESSION"}


class HostError(Exception):
    pass


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


class FirmwareLike:
    """In-process model of the v2 firmware state machine (WAIT_SESSION -> PUBLISH
    -> WAIT_ACK -> ...). A fresh instance equals a fresh boot."""

    def __init__(self, pattern, inject_bad_crc_at=None, stuck=False):
        self.pattern = pattern
        self.inject_bad_crc_at = inject_bad_crc_at
        self.stuck = stuck
        self.mailbox = bytearray(STRUCT_BYTES)
        struct.pack_into("<2I", self.mailbox, 0, MAILBOX_MAGIC, MAILBOX_VERSION)
        self.status = MB_IDLE
        self.session = 0
        self.seq = 0
        self.offset = 0
        self.length = 0

    def read(self, off, n):
        if off < 0 or off + n > STRUCT_BYTES:
            raise HostError("sim read out of range")
        return bytes(self.mailbox[off:off + n])

    def write(self, off, data):
        if off not in (SESSION_OFF, ACK_OFF):
            raise HostError(f"sim write to disallowed offset {off}")
        self.mailbox[off:off + len(data)] = data
        if off == SESSION_OFF:
            self.session = struct.unpack("<I", data)[0]
            if self.session != 0 and self.status == MB_IDLE:
                self._publish()
        elif off == ACK_OFF and not self.stuck:
            ack = struct.unpack("<I", data)[0]
            if self.status == MB_READY and ack == self.seq:
                self.offset += self.length
                if self.offset >= NOR_TOTAL_SIZE:
                    struct.pack_into("<I", self.mailbox, 24, MB_DONE)
                    self.status = MB_DONE
                else:
                    self._publish()

    def _publish(self):
        n = min(NOR_BLOCK_SIZE, NOR_TOTAL_SIZE - self.offset)
        self.length = n
        self.seq += 1
        struct.pack_into("<I", self.mailbox, 24, MB_BUSY)  # 1. leave READY
        data = self.pattern(self.offset, n)
        if self.inject_bad_crc_at is not None and self.offset == self.inject_bad_crc_at:
            crc = crc32(data) ^ 0xFFFFFFFF
        else:
            crc = crc32(data)
        self.mailbox[HEADER_BYTES:HEADER_BYTES + n] = data       # 2. buffer
        struct.pack_into("<3I", self.mailbox, 8, self.session, self.seq, self.offset)
        struct.pack_into("<I", self.mailbox, 20, n)
        struct.pack_into("<I", self.mailbox, 28, 0)
        struct.pack_into("<I", self.mailbox, 32, crc)            # 3. header fields
        struct.pack_into("<I", self.mailbox, 24, MB_READY)       # 4. publish last
        self.status = MB_READY


class SwdFirmware:
    """Real firmware mailbox over STM32_Programmer_CLI. Only SESSION_OFF and
    ACK_OFF may be written; addresses use explicit 0x hex; logs and read buffers
    land in this pass's own directory and are never overwritten."""

    def __init__(self, cli, base, run_dir):
        self.cli = cli
        self.base = base
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if base % 4 != 0:
            raise HostError(f"mailbox address not 4-byte aligned: {base:#x}")
        if not (SRAM_BASE <= base and base + STRUCT_BYTES <= SRAM_END):
            raise HostError(f"mailbox outside SRAM: {base:#x}")
        self._n = 0
        self._tag = os.urandom(8).hex()

    def _run(self, args):
        self._n += 1
        log_path = self.run_dir / f"cli-{self._tag}-{self._n:05d}.log"
        started = time.monotonic()
        event = {"args": [self.cli] + args,
                 "started_at": datetime.now().astimezone().isoformat(),
                 "log": log_path.name}
        stdout = stderr = b""
        problem = None
        try:
            proc = subprocess.run([self.cli] + args, capture_output=True, timeout=120)
            stdout, stderr = proc.stdout, proc.stderr
            event["returncode"] = proc.returncode
            if proc.returncode:
                problem = f"CLI exit {proc.returncode}: {stderr[:200]!r}"
        except (subprocess.TimeoutExpired, OSError) as exc:
            stdout = getattr(exc, "stdout", None) or b""
            stderr = getattr(exc, "stderr", None) or str(exc).encode("utf-8")
            problem = f"CLI {type(exc).__name__}: {exc}"
            event["exception"] = type(exc).__name__
        event["duration_s"] = time.monotonic() - started
        event["error"] = problem
        if isinstance(stdout, str): stdout = stdout.encode("utf-8")
        if isinstance(stderr, str): stderr = stderr.encode("utf-8")
        with log_path.open("xb") as output:
            output.write(stdout + b"\n" + stderr)
        with (self.run_dir / "cli-events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(event) + "\n")
        if problem:
            raise HostError(problem)
        return stdout

    def reset(self):
        self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000", "-rst"])
        time.sleep(2.0)

    def read(self, off, n):
        if off < 0 or n <= 0 or off > STRUCT_BYTES or n > STRUCT_BYTES - off:
            raise HostError(f"read out of mailbox range: {off}+{n}")
        buf_path = self.run_dir / f"read-{self._tag}-{self._n+1:05d}-{off:04x}-{n:04x}.bin"
        if buf_path.exists():
            raise HostError("read path already exists")
        self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                   "-u", hex(self.base + off), hex(n), str(buf_path)])
        try:
            data = buf_path.read_bytes()
        except OSError as exc:
            raise HostError(f"CLI did not produce a readable new upload: {exc}") from exc
        if len(data) != n:
            raise HostError(f"short read: got {len(data)}, want {n}")
        return data

    def write(self, off, data):
        if off not in (SESSION_OFF, ACK_OFF) or len(data) != 4:
            raise HostError(f"write to disallowed mailbox field/size: {off}/{len(data)}")
        value = struct.unpack("<I", data)[0]
        self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                   "-w32", hex(self.base + off), hex(value)])


def _unpack_header(raw):
    if len(raw) != HEADER_BYTES:
        raise HostError(f"short header: {len(raw)} bytes, expected {HEADER_BYTES}")
    return struct.unpack(HDR, raw)


def read_pass(fw, out_path, timeout_s, session, limit_blocks=0):
    """One full pass with strict v2 checks. Raises HostError on any failure."""
    with open(out_path, "wb") as buf:
        expected = 0
        expected_seq = 1
        deadline = time.monotonic() + timeout_s
        while True:
            if time.monotonic() > deadline:
                raise HostError(f"total timeout at offset {expected}")
            magic, version, sess, seq, offset, length, status, error, crc, ack = \
                _unpack_header(fw.read(0, HEADER_BYTES))
            if magic != MAILBOX_MAGIC or version != MAILBOX_VERSION:
                raise HostError(f"bad magic/version {magic:#x}/{version}")
            if sess != session:
                raise HostError(f"session mismatch: got {sess:#x}, want {session:#x}")
            if status == MB_ERROR:
                raise HostError(f"firmware error {ERR_NAME.get(error, error)}")
            if error:
                raise HostError(f"nonzero error in status {status}: {error}")
            if status == MB_DONE:
                if seq != expected_seq-1 or ack != seq or not (0 < length <= NOR_BLOCK_SIZE) or offset+length != NOR_TOTAL_SIZE:
                    raise HostError("inconsistent DONE header")
                if expected != NOR_TOTAL_SIZE:
                    raise HostError(f"DONE at {expected} bytes, expected {NOR_TOTAL_SIZE}")
                break
            if status in (MB_IDLE, MB_BUSY):
                time.sleep(0.005)
                continue
            if status != MB_READY:
                raise HostError(f"unexpected status {status:#x}")
            if seq < expected_seq:
                # Firmware has not advanced past the acked block yet: keep waiting.
                time.sleep(0.005)
                continue
            if seq > expected_seq:
                raise HostError(f"seq {seq} jumped past expected {expected_seq}")
            if offset != expected:
                raise HostError(f"offset {offset:#x} != expected {expected:#x}")
            if not (0 < length <= NOR_BLOCK_SIZE):
                raise HostError(f"bad length {length}")
            if offset + length > NOR_TOTAL_SIZE:
                raise HostError(f"block overruns total: {offset:#x}+{length}")
            data = fw.read(HEADER_BYTES, length)
            if len(data) != length:
                raise HostError(f"short block read: {len(data)}/{length}")
            if crc32(data) != crc:
                raise HostError(f"CRC mismatch at offset {offset:#x}")
            # Re-read the header and confirm it did not change mid-block.
            h2 = _unpack_header(fw.read(0, HEADER_BYTES))
            if h2 != (magic, version, sess, seq, offset, length, status, error, crc, ack):
                raise HostError("header changed mid-block")
            buf.write(data)
            fw.write(ACK_OFF, struct.pack("<I", seq))
            expected += length
            expected_seq += 1
            if seq % 32 == 0 or limit_blocks:
                print(f"PROGRESS bytes={expected}/{NOR_TOTAL_SIZE} seq={seq}", flush=True)
            if limit_blocks and seq >= limit_blocks:
                return expected
        return expected


def _new_session():
    while True:
        v = int.from_bytes(os.urandom(4), "little")
        if v != 0:
            return v


def _finalize(run_dir, results, probe_blocks=0, hardware=False):
    if probe_blocks:
        ok = len(results) == 1 and results[0]["ok"] and results[0]["bytes"] == probe_blocks * NOR_BLOCK_SIZE
        manifest = {"status": "probe_passed" if ok else "partial", "complete_backup": False,
                    "probe_blocks": probe_blocks, "passes": results}
    else:
        ok = len(results) == 2 and all(p["ok"] for p in results)
        match = ok and all(p["bytes"] == NOR_TOTAL_SIZE for p in results) and results[0]["sha256"] == results[1]["sha256"]
        ok = bool(match)
        manifest = {"status": "passed" if match else ("mismatch" if len(results)==2 and all(p["ok"] for p in results) else "partial"),
                    "complete_backup": bool(match), "total_bytes": NOR_TOTAL_SIZE,
                    "passes": results, "sha256_match": bool(match)}
        if match: manifest["sha256"] = results[0]["sha256"]
    manifest["hardware_mode"] = bool(hardware)
    manifest["finished_at"] = datetime.now().astimezone().isoformat()
    tmp = run_dir / "manifest.json.tmp"
    with tmp.open("x", encoding="utf-8") as output:
        output.write(json.dumps(manifest, indent=2) + "\n")
        output.flush(); os.fsync(output.fileno())
    os.replace(tmp, run_dir / "manifest.json")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0 if ok else 1


def _hardware_build(base):
    import importlib.util
    lab = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("nor_backup_build_guard", lab / "build_nor.py")
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    record = json.loads((lab / "evidence/nor-backup-build.json").read_text(encoding="utf-8"))
    if record.get("status") != "ready_for_review" or record.get("stage_input_sha256") != build.stage_hashes():
        raise HostError("Build inputs changed: rebuild this stage before hardware access")
    if record.get("core_hashes") != build.check_core_frozen():
        raise HostError("Frozen core binding changed")
    mb = record["mailbox"]
    if int(mb["address"], 0) != base or mb["struct_bytes"] != STRUCT_BYTES or mb["header_bytes"] != HEADER_BYTES:
        raise HostError("Mailbox differs from the matched ELF build record")
    for item in record["artifacts"]:
        p = lab / item["path"]
        if p.stat().st_size != item["bytes"] or build.digest(p) != item["sha256"]:
            raise HostError("Firmware artifact changed")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hardware", action="store_true")
    ap.add_argument("--cli", default=r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe")
    ap.add_argument("--mailbox-addr", default=None)
    ap.add_argument("--out-base", default=r"D:\Project\Can\stages\board-lab\evidence\nor")
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--probe-blocks", type=int, default=0, help="1..8 blocks, one pass only; never a complete backup")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest: return selftest()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or not 0 <= args.probe_blocks <= 8:
        print("ERROR: finite positive timeout and 0..8 probe blocks required"); return 2
    mailbox = None
    if args.hardware:
        try:
            if args.mailbox_addr is None: raise HostError("--hardware requires --mailbox-addr")
            mailbox = int(args.mailbox_addr, 0)
            if mailbox % 4 or not SRAM_BASE <= mailbox <= SRAM_END-STRUCT_BYTES:
                raise HostError("Mailbox alignment/SRAM check failed")
            _hardware_build(mailbox)
        except (HostError, OSError, ValueError, KeyError) as exc:
            print(f"PRECHECK FAILED: {exc}"); return 2
    base = Path(args.out_base); base.mkdir(parents=True, exist_ok=True)
    run_dir = base / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(exist_ok=False)
    pattern = lambda off, n: bytes(((off+i)*131+7)&0xFF for i in range(n))
    results = []; sessions = set()
    for pass_no in ((1,) if args.probe_blocks else (1,2)):
        pass_dir = run_dir / f"pass{pass_no}"; pass_dir.mkdir(exist_ok=False)
        session = _new_session()
        while session in sessions: session = _new_session()
        sessions.add(session)
        out_bin = pass_dir / f"pass{pass_no}.bin"
        started = time.monotonic()
        try:
            if args.hardware:
                fw = SwdFirmware(args.cli, mailbox, pass_dir); fw.reset()
            else: fw = FirmwareLike(pattern)
            fw.write(SESSION_OFF, struct.pack("<I", session))
            written = read_pass(fw, out_bin, args.timeout, session, args.probe_blocks)
            with out_bin.open("r+b") as output: output.flush(); os.fsync(output.fileno())
            results.append({"pass": pass_no, "ok": True, "bytes": written, "session": hex(session),
                            "duration_s": time.monotonic()-started, "error": None,
                            "sha256": hashlib.sha256(out_bin.read_bytes()).hexdigest()})
        except (HostError, OSError, ValueError, struct.error, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            results.append({"pass": pass_no, "ok": False, "bytes": out_bin.stat().st_size if out_bin.exists() else 0,
                            "session": hex(session), "duration_s": time.monotonic()-started,
                            "error": f"{type(exc).__name__}: {exc}",
                            "sha256": hashlib.sha256(out_bin.read_bytes()).hexdigest() if out_bin.exists() else None})
            break
    return _finalize(run_dir, results, args.probe_blocks, args.hardware)


def selftest():
    print("[nor backup offline selftest]")
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="nor-selftest-"))
    pattern = lambda off, n: bytes(((off + i) * 131 + 7) & 0xFF for i in range(n))

    fw = FirmwareLike(pattern)
    fw.write(SESSION_OFF, struct.pack("<I", 0x12345678))
    out = d / "pass.bin"
    written = read_pass(fw, out, 60, 0x12345678)
    if written != NOR_TOTAL_SIZE:
        raise AssertionError(f"happy path wrote {written}")
    if out.stat().st_size != NOR_TOTAL_SIZE:
        raise AssertionError("happy path size")
    print("  happy path: OK (16 MiB)")

    fw = FirmwareLike(pattern, inject_bad_crc_at=0)
    fw.write(SESSION_OFF, struct.pack("<I", 1))
    try:
        read_pass(fw, d / "badcrc.bin", 10, 1)
        raise AssertionError("bad CRC accepted")
    except HostError as e:
        if "CRC" not in str(e):
            raise
    print("  bad CRC rejected: OK")

    fw = FirmwareLike(pattern)
    fw.write(SESSION_OFF, struct.pack("<I", 0xABCD))
    try:
        read_pass(fw, d / "sess.bin", 10, 0x9999)
        raise AssertionError("session mismatch accepted")
    except HostError as e:
        if "session" not in str(e):
            raise
    print("  session mismatch rejected: OK")

    fw = FirmwareLike(pattern, stuck=True)
    fw.write(SESSION_OFF, struct.pack("<I", 1))
    try:
        read_pass(fw, d / "timeout.bin", 0.2, 1)
        raise AssertionError("timeout not raised")
    except HostError as e:
        if "timeout" not in str(e):
            raise
    print("  timeout -> partial: OK")

    print("ALL SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
