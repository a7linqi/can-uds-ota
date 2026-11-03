"""Fake-CLI integration tests for the real SwdFirmware adapter and read_pass.

Patches subprocess.run so no real CLI or probe is ever touched. Verifies the
argument lists (explicit 0x hex), the write allow-list (only session/ack), the
mailbox address checks, and the read_pass failure paths (non-zero CLI, short
read, sequence jump, wrong DONE, session mismatch).
"""
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

LAB = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("nor_host", LAB / "host/nor_backup.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def make_swd(state):
    """Build a SwdFirmware whose subprocess.run is replaced by a recorder."""
    calls = []

    def fake_run(args, capture_output=True, timeout=120):
        calls.append(args)
        if state.get("timeout"):
            raise subprocess.TimeoutExpired(args, timeout)
        rc = state.get("returncode", 0)
        if rc != 0:
            return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"boom")
        if "-u" in args:
            i = args.index("-u")
            size = int(args[i + 2])
            path = args[i + 3]
            data = state.get("data", b"\x00" * 40)
            Path(path).write_bytes(data[:size])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    fw = host.SwdFirmware(state.get("cli", "FAKE_CLI"), state.get("base", 0x20000278),
                          state.get("dir", LAB / "build"))
    fw._run = lambda args: _run_recorder(fw, args, state, calls) if False else None
    return fw, calls, fake_run


def _run_recorder(fw, args, state, calls):
    raise AssertionError("unused")


def make_fw(base=0x20000278):
    fw = host.SwdFirmware("FAKE_CLI", base, LAB / "build")
    return fw


def test():
    print("[fake-CLI integration tests]")
    import unittest.mock as mock
    import tempfile

    # 1. write allow-list + hex encoding
    calls = []
    fw = make_fw()
    with mock.patch.object(fw, "_run", side_effect=lambda args: calls.append(args)):
        fw.write(host.ACK_OFF, struct.pack("<I", 7))
        fw.write(host.SESSION_OFF, struct.pack("<I", 0xDEADBEEF))
    assert len(calls) == 2
    ack_args = calls[0]
    assert "-w32" in ack_args, ack_args
    assert any(a.startswith("0x") for a in ack_args), ack_args  # hex addresses/values
    # address == base + ACK_OFF (36); value == hex(7)
    assert hex(0x20000278 + host.ACK_OFF) in ack_args, ack_args
    assert hex(7) in ack_args, ack_args
    print("  write allow-list + hex: OK")

    # 2. disallowed write offset
    try:
        fw.write(4, struct.pack("<I", 1))
        raise AssertionError("write to disallowed offset accepted")
    except host.HostError:
        pass
    print("  disallowed write rejected: OK")

    # 3. mailbox address alignment / SRAM bounds
    for bad in (0x20000279, 0x08000000, 0x2001E000):
        try:
            host.SwdFirmware("FAKE_CLI", bad, LAB / "build")
            raise AssertionError(f"bad mailbox addr accepted: {bad:#x}")
        except host.HostError:
            pass
    print("  mailbox address alignment/bounds: OK")

    # 4. read with non-zero CLI -> HostError
    fw = make_fw()
    state = {"returncode": 1}
    with mock.patch.object(host.subprocess, "run",
                           side_effect=lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"x")):
        try:
            fw.read(0, 40)
            raise AssertionError("non-zero CLI accepted")
        except host.HostError as e:
            assert "CLI exit 1" in str(e)
    print("  non-zero CLI -> error: OK")

    # 5. short read -> HostError
    fw = make_fw()
    with mock.patch.object(host.subprocess, "run",
                           side_effect=lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b"")):
        # read 40 bytes but CLI only writes 10
        fw2 = make_fw()
        def short_write(args, **k):
            if "-u" in args:
                Path(args[args.index("-u") + 3]).write_bytes(b"\x00" * 10)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(host.subprocess, "run", side_effect=short_write):
            try:
                fw2.read(0, 40)
                raise AssertionError("short read accepted")
            except host.HostError as e:
                assert "short read" in str(e)
    print("  short read -> error: OK")

    # 6. sequence jump via a hand-rolled firmware object
    class SeqJump:
        def read(self, off, n):
            if off == 0:
                return struct.pack(host.HDR, host.MAILBOX_MAGIC, host.MAILBOX_VERSION,
                                   0x1, 7, 0, 64, host.MB_READY, 0, host.crc32(b"\x00" * 64), 0)
            return b"\x00" * 64
        def write(self, off, data):
            pass
    try:
        host.read_pass(SeqJump(), LAB / "build/sj.bin", 1, 0x1)
        raise AssertionError("seq jump accepted")
    except host.HostError as e:
        assert "jumped" in str(e), e
    print("  sequence jump rejected: OK")

    # 7. wrong DONE (fewer bytes than total)
    class WrongDone:
        def read(self, off, n):
            if off == 0:
                return struct.pack(host.HDR, host.MAILBOX_MAGIC, host.MAILBOX_VERSION,
                                   0x2, 1, 0, 64, host.MB_DONE, 0, 0, 0)
            return b""
        def write(self, off, data):
            pass
    try:
        host.read_pass(WrongDone(), LAB / "build/wd.bin", 1, 0x2)
        raise AssertionError("wrong DONE accepted")
    except host.HostError as e:
        assert "DONE" in str(e), e
    print("  wrong DONE rejected: OK")

    # 8. session mismatch
    class WrongSession:
        def read(self, off, n):
            if off == 0:
                return struct.pack(host.HDR, host.MAILBOX_MAGIC, host.MAILBOX_VERSION,
                                   0x99, 1, 0, 64, host.MB_READY, 0, host.crc32(b"\x00" * 64), 0)
            return b"\x00" * 64
        def write(self, off, data):
            pass
    try:
        host.read_pass(WrongSession(), LAB / "build/ws.bin", 1, 0x2)
        raise AssertionError("session mismatch accepted")
    except host.HostError as e:
        assert "session" in str(e), e
    print("  session mismatch rejected: OK")

    print("ALL FAKE-CLI TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(test())
