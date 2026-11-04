"""End-to-end host tests for the restore drill: the compiled C state machine is
driven through the TinyCC DLL against a W25Q-behaviour chip model, including a
power cut at every erase/program/verify point of every block. Each cut must be
followed by a plain re-run that converges byte-for-byte on the image.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
BLOCK = 32768
TOTAL = 256 * 1024            # 8 blocks, fast enough to cut at every point


def load_host():
    spec = importlib.util.spec_from_file_location("nor_restore_host", LAB / "host/nor_restore.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nr = load_host()
IMAGE = bytes(((i * 29 + (i >> 7) * 17 + 3) & 0xFF) for i in range(TOTAL))


class RestoreSimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dll = nr.ctypes.CDLL(str(nr.build_sim_dll()))

    def run_restore(self, cut=None):
        return nr.run_sim(IMAGE, TOTAL, cut=cut, timeout=60.0)

    def assert_converged(self, result, label):
        self.assertEqual(result["device_crc32"], result["image_crc32"],
                         f"{label}: device CRC != image CRC")
        self.assertTrue(result["chip_matches_image"], f"{label}: chip content differs")
        self.assertEqual(result["program_without_erase"], 0,
                         f"{label}: a page was programmed before its sector erase")
        self.assertEqual(result["sectors_erased"], TOTAL // nr.SECTOR,
                         f"{label}: not every sector was erased exactly once")

    def test_happy_path(self):
        result = self.run_restore()
        self.assert_converged(result, "happy path")
        self.assertEqual(result["blocks"], TOTAL // BLOCK)
        self.assertEqual(result["pages_programmed"], TOTAL // nr.PAGE)
        self.assertEqual(result["attempts_count"], 1)
        self.assertEqual(result["chip_sha256"], nr.sha256_bytes(IMAGE))

    def test_power_cut_at_every_block_and_phase(self):
        cases = 0
        for block in range(TOTAL // BLOCK):
            for phase in ("erase", "program", "verify"):
                cut = {"at": block * BLOCK, "phase": phase,
                       "label": f"block {block} {phase}"}
                result = self.run_restore(cut)
                self.assert_converged(result, f"cut {block}/{phase}")
                self.assertEqual(result["attempts_count"], 2,
                                 f"cut {block}/{phase} did not force exactly one retry")
                self.assertTrue(result["attempts"][0]["cut"],
                                f"cut {block}/{phase} was not recorded")
                cases += 1
        self.assertEqual(cases, 3 * (TOTAL // BLOCK))

    def test_power_cut_during_final_crc_sweep(self):
        # A cut in the last block's read-back lands inside the final CRC sweep:
        # no bytes change, but the device still fails and must be re-run.
        cut = {"at": TOTAL - BLOCK, "phase": "verify", "label": "final sweep"}
        result = self.run_restore(cut)
        self.assert_converged(result, "final sweep cut")
        self.assertEqual(result["attempts_count"], 2)

    def test_verify_mode_is_read_only(self):
        result = nr.run_sim(IMAGE, TOTAL, mode="verify")
        self.assertEqual(result["device_crc32"], result["image_crc32"])
        self.assertEqual(result["pages_programmed"], 0)
        self.assertEqual(result["sectors_erased"], 0)

    def test_acceptance_rejects_a_crc_mismatch(self):
        result = self.run_restore()
        self.assertEqual(nr.check_sim_result(result), [])
        bad = dict(result)
        bad["device_crc32"] ^= 1
        self.assertTrue(nr.check_sim_result(bad), "a device CRC mismatch was accepted")
        bad = dict(result)
        bad["chip_matches_image"] = False
        self.assertTrue(nr.check_sim_result(bad), "a content mismatch was accepted")
        bad = dict(result)
        bad["program_without_erase"] = 1
        self.assertTrue(nr.check_sim_result(bad), "an erase-before-program violation was accepted")

    def test_two_runs_are_deterministic(self):
        a = self.run_restore()
        b = self.run_restore()
        self.assertEqual(a["chip_sha256"], b["chip_sha256"])
        self.assertEqual(a["device_crc32"], b["device_crc32"])

    def test_cut_left_a_dirty_chip_that_a_rerun_repairs(self):
        # Sanity: the cut really damaged the chip, so the retry is doing work.
        link, chip, _ = nr.open_sim(TOTAL, {"at": 0, "phase": "program"})
        driver = nr.Driver(link, TOTAL, 30.0)
        with self.assertRaises(nr.PowerCut):
            driver.restore(IMAGE)
        link.close()
        self.assertNotEqual(bytes(chip.mem), IMAGE)
        self.assertTrue(chip.cut_hit)


class RestoreCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="restore-cli-", dir=LAB / "build"))
        self.image = self.tmp / "image.bin"
        self.image.write_bytes(IMAGE)

    def test_rejects_wrong_sha256(self):
        code = nr.main(["--image", str(self.image), "--expect-sha256", "00" * 32,
                        "--out-base", str(self.tmp / "runs")])
        self.assertEqual(code, 2)

    def test_rejects_wrong_crc32(self):
        code = nr.main(["--image", str(self.image), "--expect-crc32", "0x1",
                        "--out-base", str(self.tmp / "runs")])
        self.assertEqual(code, 2)

    def test_rejects_unaligned_image(self):
        bad = self.tmp / "bad.bin"
        bad.write_bytes(IMAGE[:-1])
        code = nr.main(["--image", str(bad), "--out-base", str(self.tmp / "runs")])
        self.assertEqual(code, 2)

    def test_manifest_written_and_runs_are_isolated(self):
        base = self.tmp / "runs"
        code = nr.main(["--image", str(self.image), "--out-base", str(base)])
        self.assertEqual(code, 0)
        first = sorted(p for p in base.iterdir() if p.is_dir())[0]
        manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        snapshot = (first / "manifest.json").read_bytes()
        self.assertEqual(manifest["status"], "passed")
        self.assertFalse(manifest["hardware_mode"])
        self.assertEqual(manifest["image_sha256"], nr.sha256_bytes(IMAGE))
        self.assertTrue((first / "events.jsonl").is_file())

        code = nr.main(["--image", str(self.image), "--out-base", str(base),
                        "--cut-at-block", "3", "--cut-phase", "erase"])
        self.assertEqual(code, 0)
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
        self.assertEqual(len(dirs), 2)
        self.assertEqual((dirs[0] / "manifest.json").read_bytes(), snapshot,
                         "a later run overwrote the earlier manifest")
        second = json.loads((dirs[1] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(second["attempts_count"], 2)
        self.assertEqual(second["status"], "passed")

    def test_plan_does_not_execute(self):
        code = nr.main(["--image", str(self.image), "--plan",
                        "--out-base", str(self.tmp / "runs")])
        self.assertEqual(code, 0)
        self.assertFalse((self.tmp / "runs").exists())


class RestoreGateTests(unittest.TestCase):
    def test_hardware_gate_refuses_unknown_mailbox(self):
        with self.assertRaises(nr.HostError):
            nr.hardware_gate(0x1FFF0000)

    def test_driver_rejects_mismatched_block_geometry(self):
        link, chip, block_size = nr.open_sim(TOTAL, None)
        try:
            with self.assertRaises(nr.HostError):
                nr.Driver(link, TOTAL - nr.SECTOR, 10.0)
        finally:
            link.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
