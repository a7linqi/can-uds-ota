"""Host driver for the NOR restore drill (mailbox protocol v3).

Two backends share one driver loop:

  sim       builds the real restore state machine (firmware/nor_restore.c) into
            a TinyCC DLL and drives it through ctypes against a chip model with
            W25Q semantics (erase -> 0xFF, program only clears bits, page and
            sector rules, read-back verify). A power cut is modelled by letting
            the in-flight erase/program take partial effect and then failing
            every later transfer; the next attempt starts from a brand new
            device context, exactly like a rebooted MCU.

  hardware  drives the flashed firmware over the SWD RAM mailbox with
            STM32_Programmer_CLI, and refuses to run unless the linked image is
            provably the one this source tree produced (build record binding).

Acceptance for both: the device's own full-chip CRC32 (computed by reading the
whole chip back through SPI) must equal the CRC32 of the golden image, and the
per-block echo must match what was submitted. A restore that only "wrote
without error" is not accepted.
"""
import argparse
import ctypes
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
CORE = Path(r"D:\Project\Can\ota-core")
TCC = r"D:\Project\Can\toolchain\tccbox-2025.10.27.data\purelib\tccbox\tcc_dist\tcc.exe"

NORD = Path(r"D:\Project\Can\backups\nor-original\full\20261103_200408_779736\pass1\pass1.bin")
GOLDEN_SHA256 = "929af96ac660590418cb74878033b742bcd31d9e7108933ce769548f40e305e1"
GOLDEN_CRC32 = 0xA4188821

# ---- protocol v3 (must match firmware/nor_restore.h) ----------------------
MAGIC = 0x4E4F5257
VERSION = 3
HDR = "<10I"
HEADER_BYTES = 40
OFF_SESSION, OFF_SEQ, OFF_OFFSET, OFF_LENGTH = 8, 12, 16, 20
OFF_STATUS, OFF_ERROR, OFF_CRC32, OFF_ACK, OFF_DATA = 24, 28, 32, 36, 40
CMD_RESTORE = 0xA1000000
CMD_VERIFY = 0xA2000000
CMD_RESTORE_RANGE = 0xA3000000
ST_IDLE, ST_BUSY, ST_READY, ST_BLOCK_OK, ST_DONE, ST_ERROR = 0, 1, 2, 3, 4, 0xE0
ERR_NAME = {
    0: "NONE", 1: "INIT", 2: "JEDEC", 3: "SPI", 4: "BAD_REQUEST",
    5: "BLOCK_CRC", 6: "ERASE", 7: "PROGRAM", 8: "VERIFY",
}
SECTOR = 4096
PAGE = 256
SRAM_BASE, SRAM_END = 0x20000000, 0x20020000
BLOCK_HINT = 32768   # protocol default; the real value comes from the build record


class HostError(Exception):
    pass


class PowerCut(Exception):
    pass


class DeviceError(HostError):
    def __init__(self, code, where=""):
        super().__init__(f"device error {ERR_NAME.get(code, code)}"
                         + (f" at {where}" if where else ""))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def new_session():
    while True:
        value = int.from_bytes(os.urandom(4), "little")
        if value and value <= 0x7FFFFFFF:
            return value


# --------------------------------------------------------------------------
# Chip model with W25Q semantics
# --------------------------------------------------------------------------
class ChipModel:
    def __init__(self, total, fill=0x00, cut=None, seed=None):
        self.total = total
        self.mem = bytearray([fill & 0xFF]) * total
        if seed is not None:
            if len(seed) != total:
                raise HostError("seed size != chip size")
            self.mem[:] = seed
        self.erased = [False] * (total // SECTOR)
        self.erases = self.programs = self.reads = 0
        self.violations = 0          # programs without a preceding sector erase
        self.cut = cut               # {"at": int, "phase": str, "label": str}
        self.cut_hit = False

    # -- power cut ------------------------------------------------------
    def _cut_now(self, addr, phase):
        cut = self.cut
        if not cut or self.cut_hit or cut["phase"] != phase:
            return False
        if addr < cut["at"]:
            return False
        self.cut_hit = True
        return True

    # -- chip operations ------------------------------------------------
    def erase(self, addr):
        if self._cut_now(addr, "erase"):
            self.mem[addr:addr + SECTOR // 2] = b"\xff" * (SECTOR // 2)
            return -1
        if addr % SECTOR or addr + SECTOR > self.total:
            return -1
        self.mem[addr:addr + SECTOR] = b"\xff" * SECTOR
        self.erased[addr // SECTOR] = True
        self.erases += 1
        return 0

    def program(self, addr, data):
        if self._cut_now(addr, "program"):
            half = len(data) // 2
            self._and_into(addr, data[:half])
            return -1
        if addr % PAGE or not 0 < len(data) <= PAGE or addr + len(data) > self.total:
            return -1
        if not self.erased[addr // SECTOR]:
            self.violations += 1
        self._and_into(addr, data)
        self.programs += 1
        return 0

    def _and_into(self, addr, data):
        cur = bytes(self.mem[addr:addr + len(data)])
        if cur == b"\xff" * len(data):
            self.mem[addr:addr + len(data)] = data
        elif cur == data:
            pass
        else:
            a = int.from_bytes(cur, "little")
            b = int.from_bytes(data, "little")
            self.mem[addr:addr + len(data)] = (a & b).to_bytes(len(data), "little")

    def read(self, addr, n):
        if self._cut_now(addr, "verify"):
            return None
        if addr + n > self.total:
            return None
        self.reads += 1
        return bytes(self.mem[addr:addr + n])

    def chip_crc32(self):
        return crc32(self.mem)

    def chip_sha256(self):
        return sha256_bytes(self.mem)


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------
class SimLink:
    """Drives the compiled C state machine through the TinyCC DLL."""

    ERASE_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)
    PROGRAM_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32, ctypes.c_void_p)
    READ_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
                               ctypes.c_uint32, ctypes.c_void_p)

    def __init__(self, dll, chip, total):
        self.dll = dll
        self.chip = chip
        self.total = total
        self._e = self.ERASE_FN(self._erase)
        self._p = self.PROGRAM_FN(self._program)
        self._r = self.READ_FN(self._read)
        dll.nr_sim_create.restype = ctypes.c_void_p
        dll.nr_sim_create.argtypes = [ctypes.c_uint32, self.ERASE_FN, self.PROGRAM_FN,
                                      self.READ_FN, ctypes.c_void_p]
        self.handle = dll.nr_sim_create(total, self._e, self._p, self._r, None)
        if not self.handle:
            raise HostError("nr_sim_create refused the geometry")
        dll.nr_sim_service.restype = ctypes.c_int
        dll.nr_sim_service.argtypes = [ctypes.c_void_p]
        dll.nr_sim_mailbox.restype = ctypes.c_void_p
        dll.nr_sim_mailbox.argtypes = [ctypes.c_void_p]
        dll.nr_sim_destroy.argtypes = [ctypes.c_void_p]
        base = dll.nr_sim_mailbox(self.handle)
        self.mb = (ctypes.c_uint8 * (HEADER_BYTES + self.block_size)).from_address(base)

    @property
    def block_size(self):
        return self.dll.nr_sim_block_size()

    # callbacks ---------------------------------------------------------
    def _erase(self, addr, user):
        return self.chip.erase(addr)

    def _program(self, addr, src, n, user):
        data = ctypes.string_at(src, n)
        return self.chip.program(addr, data)

    def _read(self, addr, out, n, user):
        data = self.chip.read(addr, n)
        if data is None:
            return -1
        ctypes.memmove(out, data, n)
        return 0

    # link API ----------------------------------------------------------
    def step(self):
        return self.dll.nr_sim_service(self.handle)

    def u32(self, off):
        return struct.unpack_from("<I", self.mb, off)[0]

    def set_u32(self, off, value, tolerate=False):
        struct.pack_into("<I", self.mb, off, value & 0xFFFFFFFF)

    def refresh(self):
        """Advance the machine one step, like one SWD header read would."""
        self.step()

    @property
    def power_lost(self):
        return self.chip.cut_hit

    def put_data(self, offset, data):
        ctypes.memmove(ctypes.addressof(self.mb) + OFF_DATA, bytes(data), len(data))

    def close(self):
        if self.handle:
            self.dll.nr_sim_destroy(self.handle)
            self.handle = None


class SwdLink:
    """Drives the flashed firmware over the SWD RAM mailbox."""

    WRITE_WORDS = (OFF_SESSION, OFF_SEQ, OFF_OFFSET, OFF_LENGTH, OFF_CRC32, OFF_ACK)

    def __init__(self, cli, base, block_size, run_dir, poll_s=0.2):
        self.cli = cli
        self.base = base
        self.block_size = block_size
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.poll_s = poll_s
        self._n = 0
        self._tag = os.urandom(6).hex()
        self._hdr = bytearray(HEADER_BYTES)
        if base % 4:
            raise HostError(f"mailbox not 4-byte aligned: {base:#x}")
        if not (SRAM_BASE <= base and base + HEADER_BYTES + block_size <= SRAM_END):
            raise HostError(f"mailbox outside SRAM: {base:#x}")

    # transport ---------------------------------------------------------
    def _run(self, args):
        self._n += 1
        log = self.run_dir / f"cli-{self._tag}-{self._n:05d}.log"
        started = time.monotonic()
        event = {"args": [self.cli] + args,
                 "started_at": datetime.now().astimezone().isoformat(),
                 "log": log.name}
        problem = None
        try:
            proc = subprocess.run([self.cli] + args, capture_output=True, timeout=180)
            out, err = proc.stdout, proc.stderr
            event["returncode"] = proc.returncode
            if proc.returncode:
                problem = f"CLI exit {proc.returncode}: {err[:200]!r}"
        except (subprocess.TimeoutExpired, OSError) as exc:
            out = getattr(exc, "stdout", b"") or b""
            err = str(exc).encode()
            problem = f"CLI {type(exc).__name__}: {exc}"
        event["duration_s"] = time.monotonic() - started
        event["error"] = problem
        log.write_bytes(out + b"\n" + err)
        with (self.run_dir / "cli-events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
        if problem:
            raise HostError(problem)
        return out

    def _connect(self, reset=False):
        args = ["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000"]
        if reset:
            args.append("-rst")
        self._run(args)
        if reset:
            time.sleep(2.0)

    def reset_and_arm(self):
        self._connect(reset=True)
        self._refresh()

    def _refresh(self):
        path = self.run_dir / f"hdr-{self._tag}-{self._n + 1:05d}.bin"
        if path.exists():
            raise HostError("read path already exists")
        self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                   "-u", hex(self.base), hex(HEADER_BYTES), str(path)])
        data = path.read_bytes()
        if len(data) != HEADER_BYTES:
            raise HostError(f"short header read: {len(data)}")
        self._hdr[:] = data
        return bytes(self._hdr)

    def step(self):
        time.sleep(self.poll_s)
        self._refresh()
        return 0

    def u32(self, off):
        return struct.unpack_from("<I", self._hdr, off)[0]

    def set_u32(self, off, value, tolerate=False):
        if off not in self.WRITE_WORDS:
            raise HostError(f"refusing to write header word at {off}")
        # -nv: CubeProgrammer verifies every -w32 by reading the word back, but
        # the device CLEARS ack as soon as it consumes the command, so the
        # read-back would disagree and the CLI aborts with "Failed to download
        # data". Header words are validated by the device anyway (session/seq/
        # offset/length/CRC), and a lost write ends in a timeout, not a false
        # pass. The 32 KiB payload write (-w) still verifies.
        n_before = self._n
        try:
            self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                       "-nv", "-w32", hex(self.base + off), hex(value & 0xFFFFFFFF)])
        except HostError as exc:
            # Measured on CubeProgrammer 2.23.0: -nv does NOT suppress the
            # -w32 read-back. The firmware clears ack as soon as it consumes
            # the doorbell, so the CLI read-back disagrees and it exits 1 with
            # "Failed to download data!" -- even though the word landed.
            # Tolerate ONLY that specific proven case: the log must show our
            # exact address and value were issued AND the known read-back
            # complaint. A broken link, a timeout or a refused connection is
            # still fatal, otherwise a lost doorbell looks like a done block.
            log = self.run_dir / f"cli-{self._tag}-{n_before + 1:05d}.log"
            proof = (log.read_text(encoding="utf-8", errors="replace")
                     if log.is_file() else "")
            issued = (f"w @{hex(self.base + off)}:" in proof
                      and hex(value & 0xFFFFFFFF) in proof)
            readback_only = "Failed to download data" in proof
            if not tolerate or not (issued and readback_only):
                raise HostError(f"uncertain -w32 write at {off:#x}: {exc}")
        else:
            struct.pack_into("<I", self._hdr, off, value & 0xFFFFFFFF)

    def refresh(self):
        self._refresh()

    def put_data(self, offset, data):
        # Reuse ONE scratch file for the whole run and never delete it.
        # Deleting one temp file per block hit the environment's bulk-delete
        # guard at exactly 50 deletions and killed the 2026-11-05 512-block
        # run at block 49 (no manifest was written). Overwriting the same
        # 32 KiB file keeps the transport identical and performs zero deletes.
        tmp = self.run_dir / f"blk-{self._tag}-scratch.bin"
        tmp.write_bytes(bytes(data))
        self._run(["-c", "port=SWD", "index=0", "mode=HOTPLUG", "freq=1000",
                   "-w", str(tmp), hex(self.base + OFF_DATA)])

    @property
    def power_lost(self):
        return False

    def close(self):
        pass


# --------------------------------------------------------------------------
# Driver (protocol logic, backend independent)
# --------------------------------------------------------------------------
class Driver:
    def __init__(self, link, total, block_timeout_s, events_path=None):
        self.link = link
        self.total = total
        self.block_size = link.block_size
        self.block_timeout = block_timeout_s
        self.events_path = events_path
        if total % SECTOR:
            raise HostError("total must be a multiple of 4096")
        if total % self.block_size:
            raise HostError("total must be a multiple of the block size")

    def _event(self, record):
        if self.events_path:
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

    def _check_identity(self):
        magic, version = self.link.u32(0), self.link.u32(4)
        if magic != MAGIC or version != VERSION:
            raise HostError(f"bad mailbox identity {magic:#x}/{version} "
                            f"(is the restore image flashed?)")

    def _pump(self, done, timeout, where=""):
        """Poll until done(status) is true. A plain "status == BLOCK_OK" check is
        not enough: after a commit that word is already set, so the next block
        would be acknowledged without the device ever seeing it. The predicate
        also requires ack to be back to 0, which only the device can do."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.link.u32(OFF_STATUS)
            if status == ST_ERROR:
                if self.link.power_lost:
                    raise PowerCut()
                raise DeviceError(self.link.u32(OFF_ERROR), where)
            if done(status):
                return status
            if time.monotonic() > deadline:
                if self.link.power_lost:
                    raise PowerCut()
                raise HostError(f"timeout at {where} (stuck at status {status:#x}, "
                                f"device {self.link.u32(OFF_OFFSET):#x})")
            self.link.step()

    def _confirm(self, accepted, where, timeout=10.0):
        """Confirm from the device side that the command was taken.

        Measured 2026-11-05 with a one-block hardware probe: the firmware
        finishes a 32 KiB block in ~0.8 s, while one CubeProgrammer header
        read can take 0.875 s late in a long run. A single read that straddles
        the BUSY window comes back as BLOCK_OK with ack already cleared -- the
        block is DONE, not rejected. The old one-shot check accepted only
        BUSY/DONE and turned that into "device did not take the command" at
        block 478 of 512 on 2026-11-04.

        So accept anything in `accepted`, plus "already finished" (BLOCK_OK
        with the doorbell consumed). Require two consecutive reads to agree
        before judging, which also rejects a torn 40-byte header. A genuinely
        lost doorbell still cannot pass the final full-chip CRC sweep.
        """
        deadline = time.monotonic() + timeout
        previous = None
        while True:
            self.link.refresh()
            status = self.link.u32(OFF_STATUS)
            ack = self.link.u32(OFF_ACK)
            if status == ST_ERROR:
                if self.link.power_lost:
                    raise PowerCut()
                raise DeviceError(self.link.u32(OFF_ERROR), where)
            if (status, ack) == previous:
                if status in accepted or (status == ST_BLOCK_OK and ack == 0):
                    return status
            previous = (status, ack)
            if time.monotonic() > deadline:
                raise HostError(f"device did not take the command at {where} "
                                f"(status {status:#x}, ack {ack:#x})")
            time.sleep(0.02)

    def arm(self, session, mode, start=None, length=None):
        self._check_identity()
        self.link.set_u32(OFF_SESSION, session)
        if mode == CMD_RESTORE_RANGE:
            # The device only looks at these once the command word lands, so
            # the order here is what makes the arm atomic from its side.
            if start is None or length is None:
                raise HostError("range arm needs start and length")
            self.link.set_u32(OFF_OFFSET, start)
            self.link.set_u32(OFF_LENGTH, length)
        self.link.set_u32(OFF_ACK, mode, tolerate=True)
        self._confirm({ST_BUSY, ST_READY, ST_DONE}, "session start")
        if mode == CMD_VERIFY:
            self._pump(lambda st: st == ST_DONE, self.block_timeout, "verify sweep")
        else:
            self._pump(lambda st: st == ST_READY, self.block_timeout, "session start")

    def submit(self, seq, offset, block):
        link = self.link
        link.put_data(offset, block)
        link.set_u32(OFF_SEQ, seq)
        link.set_u32(OFF_OFFSET, offset)
        link.set_u32(OFF_LENGTH, len(block))
        link.set_u32(OFF_CRC32, crc32(block))
        link.set_u32(OFF_ACK, seq, tolerate=True)   # last store: submits the block
        self._confirm({ST_BUSY, ST_DONE}, f"block {seq} submit")
        status = self._pump(
            lambda st: st == ST_DONE or (st == ST_BLOCK_OK and link.u32(OFF_ACK) == 0),
            self.block_timeout, f"block {seq} @ {offset:#x}")
        if status == ST_DONE:
            return status
        echo = (link.u32(OFF_SEQ), link.u32(OFF_OFFSET), link.u32(OFF_LENGTH),
                link.u32(OFF_CRC32))
        want = (seq, offset, len(block), crc32(block))
        if echo != want:
            raise HostError(f"block {seq} echo {echo} != submitted {want}")
        return status

    def restore(self, image, progress=None):
        session = new_session()
        started = time.monotonic()
        self.arm(session, CMD_RESTORE)
        offset = 0
        seq = 0
        while offset < self.total:
            n = min(self.block_size, self.total - offset)
            block = image[offset:offset + n]
            seq += 1
            t0 = time.monotonic()
            status = self.submit(seq, offset, block)
            self._event({"seq": seq, "offset": offset, "length": n,
                         "crc32": f"{crc32(block):08x}",
                         "duration_s": round(time.monotonic() - t0, 4),
                         "status": "done" if status == ST_DONE else "ok"})
            if progress:
                progress(seq, offset + n, self.total)
            if status == ST_DONE:
                break
            offset += n
        return self.finish(started, seq)

    def verify(self):
        session = new_session()
        started = time.monotonic()
        self.arm(session, CMD_VERIFY)
        return self.finish(started, 0)

    def restore_range(self, data, start, progress=None):
        """Rewrite only [start, start+len(data)); everything else is untouched.

        Same block protocol as a full restore, so the per-block echo, the
        read-back verify and the device-side CRC sweep all still apply -- the
        only difference is where the session starts and stops.
        """
        if not data:
            raise HostError("empty range")
        if start % SECTOR or len(data) % SECTOR:
            raise HostError("range start and length must be 4096-byte multiples")
        if start + len(data) > self.total:
            raise HostError("range runs past the end of the chip")
        end = start + len(data)
        session = new_session()
        started = time.monotonic()
        self.arm(session, CMD_RESTORE_RANGE, start=start, length=len(data))
        offset = start
        seq = 0
        while offset < end:
            n = min(self.block_size, end - offset)
            block = data[offset - start:offset - start + n]
            seq += 1
            t0 = time.monotonic()
            status = self.submit(seq, offset, block)
            self._event({"seq": seq, "offset": offset, "length": n,
                         "crc32": f"{crc32(block):08x}",
                         "duration_s": round(time.monotonic() - t0, 4),
                         "status": "done" if status == ST_DONE else "ok"})
            if progress:
                progress(seq, offset + n - start, len(data))
            if status == ST_DONE:
                break
            offset += n
        return self.finish(started, seq, end=end)

    def finish(self, started, seq, end=None):
        link = self.link
        # A DONE header published while the SWD read was in flight can be a
        # mixed snapshot (seen 2026-11-04: verify reported offset 0x0 twice
        # and 0x1000000 once for the same DONE state). Re-read and require the
        # two snapshots to agree before trusting any DONE field.
        first = (link.u32(OFF_STATUS), link.u32(OFF_OFFSET),
                 link.u32(OFF_LENGTH), link.u32(OFF_CRC32))
        link.refresh()
        second = (link.u32(OFF_STATUS), link.u32(OFF_OFFSET),
                  link.u32(OFF_LENGTH), link.u32(OFF_CRC32))
        if first != second:
            raise HostError(f"torn DONE header: {first} != {second}")
        status = link.u32(OFF_STATUS)
        if status != ST_DONE:
            raise HostError(f"final status {status:#x} is not DONE")
        if link.u32(OFF_ERROR) != 0:
            raise HostError("DONE carries a non-zero error code")
        offset = link.u32(OFF_OFFSET)
        want_end = self.total if end is None else end
        if offset != want_end:
            raise HostError(f"DONE offset {offset:#x} != expected end {want_end:#x}")
        if link.u32(OFF_LENGTH) != 0:
            raise HostError("DONE should carry length 0")
        return {"status": "passed", "device_crc32": link.u32(OFF_CRC32),
                "device_offset": offset, "blocks": seq,
                "duration_s": round(time.monotonic() - started, 3)}


# --------------------------------------------------------------------------
# Simulation DLL build
# --------------------------------------------------------------------------
def build_sim_dll():
    tcc = Path(TCC)
    if not tcc.is_file():
        raise HostError(f"TinyCC not found: {tcc}")
    sources = [LAB / "firmware/nor_restore.c", LAB / "firmware/nor_restore_sim.c",
               CORE / "src/core/crc32.c"]
    headers = [LAB / "firmware/nor_restore.h", CORE / "include/ota/crc32.h"]
    key = hashlib.sha256()
    for path in sources + headers:
        key.update(path.read_bytes())
    key.update(tcc.read_bytes()[:1 << 16])
    out_dir = LAB / "build/sim"
    out_dir.mkdir(parents=True, exist_ok=True)
    dll = out_dir / f"nor_restore_sim_{key.hexdigest()[:12]}.dll"
    if not dll.exists():
        cmd = [str(tcc), "-shared", "-std=c99", "-Wall",
               "-I", str(LAB / "firmware"), "-I", str(CORE / "include")]
        cmd += [str(p) for p in sources] + ["-o", str(dll)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise HostError(f"TinyCC build failed: {proc.stdout}{proc.stderr}")
    return dll


def geometry(dll):
    return {"magic": dll.nr_sim_magic(), "version": dll.nr_sim_version(),
            "header_bytes": dll.nr_sim_header_bytes(),
            "block_size": dll.nr_sim_block_size(),
            "sector_size": dll.nr_sim_sector_size(), "page_size": dll.nr_sim_page_size()}


# --------------------------------------------------------------------------
# Hardware gate
# --------------------------------------------------------------------------
def hardware_gate(mailbox):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nor_restore_build", LAB / "build_restore.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    record_path = LAB / "evidence/nor-restore-build.json"
    if not record_path.is_file():
        raise HostError("no build record: run build_restore.py first")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("status") != "ready_for_review":
        raise HostError(f"build record status is {record.get('status')!r}")
    if record.get("stage_input_sha256") != build.stage_hashes():
        raise HostError("stage inputs changed: rebuild the restore image")
    if record.get("core_hashes") != build.check_core_frozen():
        raise HostError("frozen core binding changed: rebuild")
    mb = record["mailbox"]
    if int(mb["address"], 0) != mailbox:
        raise HostError(f"mailbox address {mb['address']} != {mailbox:#x}")
    if mb["header_bytes"] != HEADER_BYTES:
        raise HostError("mailbox header size differs from the build record")
    if mb["struct_bytes"] != HEADER_BYTES + mb["block_bytes"]:
        raise HostError("mailbox struct size disagrees with the block size")
    for item in record["artifacts"]:
        path = LAB / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] \
                or sha256_bytes(path.read_bytes()) != item["sha256"]:
            raise HostError(f"firmware artifact changed: {item['path']}")
    return record


# --------------------------------------------------------------------------
# Run orchestration
# --------------------------------------------------------------------------
def load_image(path, expect_sha256):
    data = Path(path).read_bytes()
    digest = sha256_bytes(data)
    if expect_sha256 and digest != expect_sha256.lower():
        raise HostError(f"image sha256 {digest} != expected {expect_sha256}")
    return data, digest


def open_sim(total, cut, dll=None, seed=None):
    dll = dll or ctypes.CDLL(str(build_sim_dll()))
    geo = geometry(dll)
    if (geo["magic"], geo["version"], geo["header_bytes"]) != (MAGIC, VERSION, HEADER_BYTES):
        raise HostError(f"DLL protocol mismatch: {geo}")
    block_size = geo["block_size"]
    chip = ChipModel(total, fill=0x00, cut=cut, seed=seed)
    return SimLink(dll, chip, total), chip, block_size


def run_sim(image, total, cut=None, timeout=120.0, out_dir=None, mode="restore"):
    link, chip, block_size = open_sim(total, cut, seed=image if mode == "verify" else None)
    events = (Path(out_dir) / "events.jsonl") if out_dir else None
    driver = Driver(link, total, timeout, events)
    result = {"attempts": []}
    try:
        attempt = 0
        while True:
            attempt += 1
            try:
                if mode == "verify":
                    info = driver.verify()
                else:
                    info = driver.restore(image)
                result["attempts"].append({"attempt": attempt, **info, "cut": False})
                break
            except PowerCut:
                result["attempts"].append({"attempt": attempt, "cut": True})
                link.close()
                link, chip, block_size = open_sim(total, None)
                driver = Driver(link, total, timeout, events)
        result.update(info)
        result["attempts_count"] = attempt
        result["image_crc32"] = crc32(image)
        result["image_sha256"] = sha256_bytes(image)
        result["chip_crc32"] = chip.chip_crc32()
        result["chip_sha256"] = chip.chip_sha256()
        result["chip_matches_image"] = bytes(chip.mem) == image
        result["program_without_erase"] = chip.violations
        result["sectors_erased"] = chip.erases
        result["pages_programmed"] = chip.programs
        result["block_size"] = block_size
        return result
    finally:
        link.close()


def run_sim_range(image, start, length, cut=None, timeout=120.0, out_dir=None):
    """Range restore against the model.

    The chip is seeded with the golden image but with the target range wiped,
    so a pass proves two things at once: the range now matches, and every byte
    outside it was left alone (a range restore that quietly rewrote a
    neighbour would show up as a full-chip mismatch).
    """
    end = start + length
    seed = bytearray(image)
    seed[start:end] = b"\x00" * length
    seed = bytes(seed)
    link, chip, block_size = open_sim(len(image), cut, seed=seed)
    events = (Path(out_dir) / "events.jsonl") if out_dir else None
    driver = Driver(link, len(image), timeout, events)
    result = {"attempts": [], "range": {"start": start, "length": length,
                                        "crc32": f"{crc32(image[start:end]):08x}"}}
    try:
        attempt = 0
        while True:
            attempt += 1
            try:
                info = driver.restore_range(image[start:end], start)
                result["attempts"].append({"attempt": attempt, **info, "cut": False})
                break
            except PowerCut:
                result["attempts"].append({"attempt": attempt, "cut": True})
                link.close()
                link, chip, block_size = open_sim(len(image), None, seed=seed)
                driver = Driver(link, len(image), timeout, events)
        result.update(info)
        result["attempts_count"] = attempt
        result["range_crc32"] = crc32(image[start:end])
        result["chip_crc32"] = chip.chip_crc32()
        result["image_crc32"] = crc32(image)
        result["chip_matches_image"] = bytes(chip.mem) == image
        result["program_without_erase"] = chip.violations
        result["sectors_erased"] = chip.erases
        result["pages_programmed"] = chip.programs
        result["block_size"] = block_size
        return result
    finally:
        link.close()


def run_hardware(image, total, mailbox, cli, out_dir, timeout, mode):
    record = hardware_gate(mailbox)
    block_size = record["mailbox"]["block_bytes"]
    link = SwdLink(cli, mailbox, block_size, out_dir)
    driver = Driver(link, total, timeout, Path(out_dir) / "events.jsonl")
    link.reset_and_arm()
    if mode == "verify":
        print("device-side full-chip CRC sweep (%d bytes) ..." % total, flush=True)
        return driver.verify()

    blocks = -(-total // block_size)
    print("restore: %d bytes, %d blocks of %d B" % (total, blocks, block_size), flush=True)
    started = time.monotonic()

    def progress(seq, done, total_bytes):
        """One line per block: a silent 90-minute window looks like a hang."""
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (total_bytes - done) / rate / 60.0 if rate > 0 else 0.0
        print("[%4d/%4d] %6.2f%%  %6.1f s  ETA %5.1f min  %.2f KiB/s"
              % (seq, blocks, 100.0 * done / total_bytes, elapsed, eta, rate / 1024.0),
              flush=True)

    return driver.restore(image, progress=progress)


def run_hardware_range(image, start, length, mailbox, cli, out_dir, timeout, verify_full):
    record = hardware_gate(mailbox)
    block_size = record["mailbox"]["block_bytes"]
    link = SwdLink(cli, mailbox, block_size, out_dir)
    total = len(image)
    driver = Driver(link, total, timeout, Path(out_dir) / "events.jsonl")
    link.reset_and_arm()
    end = start + length
    print("range restore: [%#x, %#x), %d bytes, %d block(s) of %d B"
          % (start, end, length, -(-length // block_size), block_size), flush=True)
    started = time.monotonic()

    def progress(seq, done, total_bytes):
        print("  [%d] %#x  %.2f s" % (seq, start + done, time.monotonic() - started),
              flush=True)

    info = driver.restore_range(image[start:end], start, progress=progress)
    expected = crc32(image[start:end])
    info["range"] = {"start": start, "length": length,
                     "device_crc32": f"{info['device_crc32']:08x}",
                     "golden_crc32": f"{expected:08x}"}
    info["range_crc_match"] = info["device_crc32"] == expected
    if verify_full:
        # A range session ends in DONE and the state machine never leaves it,
        # so the full sweep needs a fresh boot. That is also what makes it a
        # clean proof: a brand-new context reads back the whole chip.
        print("full-chip CRC sweep after reset (%d bytes) ..." % total, flush=True)
        link.reset_and_arm()
        sweep = Driver(link, total, timeout, None).verify()
        info["full_chip_crc32"] = sweep["device_crc32"]
        info["full_chip_crc_match"] = sweep["device_crc32"] == crc32(image)
    return info


def finalize(out_dir, payload):
    out_dir = Path(out_dir)
    payload = dict(payload)
    payload["finished_at"] = datetime.now().astimezone().isoformat()
    tmp = out_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / "manifest.json")
    print(json.dumps(payload, indent=2), flush=True)
    return 0


def check_sim_result(result):
    problems = []
    if result["device_crc32"] != result["image_crc32"]:
        problems.append(f"device CRC {result['device_crc32']:08x} != image CRC "
                        f"{result['image_crc32']:08x}")
    if not result["chip_matches_image"]:
        problems.append("chip content differs from the image")
    if result["program_without_erase"]:
        problems.append(f"{result['program_without_erase']} pages programmed before erase")
    return problems


def check_sim_range_result(result):
    problems = []
    if result["device_crc32"] != result["range_crc32"]:
        problems.append(f"device CRC {result['device_crc32']:08x} != range CRC "
                        f"{result['range_crc32']:08x}")
    if not result["chip_matches_image"]:
        problems.append("chip content differs from the golden image after the range restore")
    if result["program_without_erase"]:
        problems.append(f"{result['program_without_erase']} pages programmed before erase")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=None)
    ap.add_argument("--expect-sha256", default=None)
    ap.add_argument("--expect-crc32", default=None,
                    help=f"expected full-chip CRC32 (golden: {GOLDEN_CRC32:08x})")
    ap.add_argument("--mode", choices=("restore", "verify"), default="restore")
    ap.add_argument("--total-size", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--hardware", action="store_true")
    ap.add_argument("--mailbox-addr", default=None)
    ap.add_argument("--cli", default=r"D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe")
    ap.add_argument("--out-base", default=str(LAB / "evidence/nor-restore"))
    ap.add_argument("--block-timeout", type=float, default=120.0)
    ap.add_argument("--cut-at-block", type=int, default=-1)
    ap.add_argument("--cut-phase", choices=("erase", "program", "verify"), default="program")
    ap.add_argument("--plan", action="store_true", help="print the plan and exit")
    ap.add_argument("--block", type=int, default=None,
                    help="restore only the block at this 0-based 32 KiB index")
    ap.add_argument("--block-count", type=int, default=1)
    ap.add_argument("--verify-full", action="store_true",
                    help="after a range restore, reset and sweep the whole chip")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.image:
        print("ERROR: --image is required", file=sys.stderr)
        return 2
    try:
        image, digest = load_image(args.image, args.expect_sha256)
        total = args.total_size if args.total_size is not None else len(image)
        if total != len(image):
            raise HostError(f"--total-size {total} != image size {len(image)}")
        if total % SECTOR or total > 0x01000000:
            raise HostError("image size must be a 4 KiB multiple and <= 16 MiB")
        if args.expect_crc32 is not None and int(args.expect_crc32, 0) != crc32(image):
            raise HostError("image CRC32 does not match --expect-crc32")
        if args.hardware and total != 0x01000000:
            raise HostError("hardware restore requires the full 16 MiB image")
        if args.plan:
            print(json.dumps({
                "image": str(args.image), "bytes": total, "sha256": digest,
                "crc32": f"{crc32(image):08x}",
                "blocks": -(-total // BLOCK_HINT), "sector_erases": total // SECTOR,
                "pages": total // PAGE, "mode": args.mode,
                "hardware": bool(args.hardware)}, indent=2))
            return 0
        base = Path(args.out_base)
        base.mkdir(parents=True, exist_ok=True)
        run_dir = base / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_dir.mkdir(parents=True, exist_ok=False)
        expected_crc = crc32(image)
        if args.block is not None:
            if args.block < 0 or args.block_count < 1:
                raise HostError("--block must be >= 0 and --block-count >= 1")
            start = args.block * BLOCK_HINT
            length = args.block_count * BLOCK_HINT
            if start + length > total:
                raise HostError(f"blocks {args.block}+{args.block_count} run past the image")
            range_crc = crc32(image[start:start + length])
            if args.hardware:
                if args.mailbox_addr is None:
                    raise HostError("--hardware requires --mailbox-addr")
                mailbox = int(args.mailbox_addr, 0)
                try:
                    info = run_hardware_range(image, start, length, mailbox, args.cli,
                                              run_dir, args.block_timeout, args.verify_full)
                except HostError as exc:
                    finalize(run_dir, {"status": "failed", "mode": "range",
                                       "hardware_mode": True, "error": str(exc),
                                       "image_sha256": digest,
                                       "range": {"start": start, "length": length}})
                    raise
                match = bool(info["range_crc_match"]) and \
                    bool(info.get("full_chip_crc_match", True))
                payload = {"status": "passed" if match else "failed", "mode": "range",
                           "hardware_mode": True, "crc_match": match,
                           "image_sha256": digest,
                           "image_crc32": f"{expected_crc:08x}",
                           "range_target_crc32": f"{range_crc:08x}",
                           **info}
                finalize(run_dir, payload)
                return 0 if match else 1
            cut = None
            if args.cut_at_block >= 0:
                cut = {"at": start + args.cut_at_block * BLOCK_HINT,
                       "phase": args.cut_phase,
                       "label": f"range block {args.cut_at_block} {args.cut_phase}"}
            try:
                result = run_sim_range(image, start, length, cut,
                                       args.block_timeout, run_dir)
            except HostError as exc:
                finalize(run_dir, {"status": "failed", "mode": "range",
                                   "hardware_mode": False, "error": str(exc),
                                   "image_sha256": digest})
                raise
            problems = check_sim_range_result(result)
            payload = {"status": "passed" if not problems else "failed", "mode": "range",
                       "hardware_mode": False, "problems": problems,
                       "crc_match": not problems, "image_sha256": digest,
                       "image_crc32": f"{expected_crc:08x}", **result}
            finalize(run_dir, payload)
            return 0 if not problems else 1
        if args.hardware:
            if args.mailbox_addr is None:
                raise HostError("--hardware requires --mailbox-addr")
            mailbox = int(args.mailbox_addr, 0)
            try:
                info = run_hardware(image, total, mailbox, args.cli, run_dir,
                                    args.block_timeout, args.mode)
            except HostError as exc:
                finalize(run_dir, {"status": "failed", "mode": args.mode,
                                   "hardware_mode": True, "error": str(exc),
                                   "image_sha256": digest,
                                   "image_crc32": f"{expected_crc:08x}"})
                raise
            # The device's own full-chip CRC is the only accepted proof.
            match = info["device_crc32"] == expected_crc
            payload = {"status": "passed" if match else "failed", "mode": args.mode,
                       "hardware_mode": True, "crc_match": match,
                       "image_sha256": digest, "image_crc32": f"{expected_crc:08x}",
                       **info}
            finalize(run_dir, payload)
            return 0 if match else 1
        cut = None
        if args.cut_at_block >= 0:
            cut = {"at": args.cut_at_block * BLOCK_HINT, "phase": args.cut_phase,
                   "label": f"block {args.cut_at_block} {args.cut_phase}"}
        try:
            result = run_sim(image, total, cut, args.block_timeout, run_dir, args.mode)
        except HostError as exc:
            finalize(run_dir, {"status": "failed", "mode": args.mode,
                               "hardware_mode": False, "error": str(exc),
                               "image_sha256": digest,
                               "image_crc32": f"{expected_crc:08x}"})
            raise
        problems = check_sim_result(result)
        payload = {"status": "passed" if not problems else "failed",
                   "mode": args.mode, "hardware_mode": False,
                   "problems": problems, "crc_match": not problems,
                   "image_sha256": digest, "image_crc32": f"{expected_crc:08x}",
                   **result}
        finalize(run_dir, payload)
        return 0 if not problems else 1
    except HostError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def selftest():
    print("[nor restore host selftest]")
    total = 64 * 1024
    image = bytes((i * 13 + 7) & 0xFF for i in range(total))

    result = run_sim(image, total)
    problems = check_sim_result(result)
    if problems:
        raise AssertionError("happy path: " + "; ".join(problems))
    if result["chip_crc32"] != result["image_crc32"]:
        raise AssertionError("chip CRC")
    print(f"  sim happy path: {result['blocks']} blocks, crc={result['device_crc32']:08x}")

    result = run_sim(image, total, cut={"at": 32768, "phase": "program"})
    problems = check_sim_result(result)
    if problems:
        raise AssertionError("cut+retry: " + "; ".join(problems))
    if result["attempts_count"] != 2:
        raise AssertionError("cut did not force a second attempt")
    print(f"  sim power cut at block 1: converged on attempt {result['attempts_count']}")

    result = run_sim(image, total, mode="verify")
    if result["device_crc32"] != result["image_crc32"]:
        raise AssertionError("verify CRC mismatch")
    print("  sim verify mode: read-only full-chip CRC matches")

    # Range restore: the seed holds the golden image with the last block wiped,
    # so matching the whole chip proves the untouched block stayed untouched.
    result = run_sim_range(image, 32768, 32768)
    problems = check_sim_range_result(result)
    if problems:
        raise AssertionError("range restore: " + "; ".join(problems))
    print(f"  sim range restore: {result['blocks']} block, device crc="
          f"{result['device_crc32']:08x}, rest of chip intact")

    result = run_sim_range(image, 32768, 32768, cut={"at": 32768, "phase": "erase"})
    problems = check_sim_range_result(result)
    if problems:
        raise AssertionError("range cut+retry: " + "; ".join(problems))
    if result["attempts_count"] != 2:
        raise AssertionError("range cut did not force a second attempt")
    print(f"  sim range power cut: converged on attempt {result['attempts_count']}")

    # The device must reject a span that runs past the end of the chip before
    # it erases anything, and it must say so through the mailbox error code.
    link, chip, _ = open_sim(total, None, seed=image)
    try:
        try:
            Driver(link, total, 30.0).arm(new_session(), CMD_RESTORE_RANGE,
                                          start=4096, length=total)
        except DeviceError as exc:
            print(f"  device rejects an out-of-range span: {exc}")
        else:
            raise AssertionError("out-of-range span was accepted")
    finally:
        link.close()
    if chip.erases:
        raise AssertionError("a rejected span must not erase anything")

    print("ALL RESTORE HOST SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
