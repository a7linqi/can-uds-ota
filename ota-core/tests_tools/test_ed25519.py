"""Independent RFC vectors and invalid encodings for the PC signing module."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ed25519 import L, Q, generate_keypair, sign, verify


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vectors(self):
        # RFC 8032 section 7.1, Ed25519 TEST 1 and TEST 2.
        vectors = [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
             "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
             "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
             "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
             "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
             "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
             "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ]
        for seed, public, message, signature in vectors:
            with self.subTest(message=message):
                expected_public = bytes.fromhex(public)
                expected_signature = bytes.fromhex(signature)
                data = bytes.fromhex(message)
                actual_public, private = generate_keypair(bytes.fromhex(seed))
                self.assertEqual(actual_public, expected_public)
                self.assertEqual(sign(data, actual_public, private), expected_signature)
                self.assertTrue(verify(expected_signature, data, expected_public))

    def fixture(self):
        public, private = generate_keypair(bytes(range(32)))
        message = b"IMG2-boundary-test" * 33
        return public, message, sign(message, public, private)

    def test_noncanonical_scalar(self):
        public, message, signature = self.fixture()
        scalar = int.from_bytes(signature[32:], "little")
        changed = signature[:32] + (scalar + L).to_bytes(32, "little")
        self.assertFalse(verify(changed, message, public))

    def test_out_of_range_scalars(self):
        public, message, signature = self.fixture()
        for value in (L, 2**256 - 1):
            with self.subTest(value=value):
                changed = signature[:32] + value.to_bytes(32, "little")
                self.assertFalse(verify(changed, message, public))

    def test_noncanonical_point_y(self):
        identity = (1).to_bytes(32, "little")
        noncanonical = (Q + 1).to_bytes(32, "little")
        self.assertFalse(verify(noncanonical + bytes(32), b"encoding", identity))
        self.assertFalse(verify(identity + bytes(32), b"encoding", noncanonical))

    def test_zero_x_with_sign_bit(self):
        identity = (1).to_bytes(32, "little")
        invalid = (1 + 2**255).to_bytes(32, "little")
        self.assertFalse(verify(invalid + bytes(32), b"encoding", identity))
        self.assertFalse(verify(identity + bytes(32), b"encoding", invalid))

    def test_modified_message(self):
        public, message, signature = self.fixture()
        self.assertFalse(verify(signature, message + b"!", public))

    def test_wrong_public_key(self):
        _, message, signature = self.fixture()
        wrong, _ = generate_keypair(bytes(range(1, 33)))
        self.assertFalse(verify(signature, message, wrong))

    def test_wrong_lengths(self):
        public, message, signature = self.fixture()
        self.assertFalse(verify(signature[:-1], message, public))
        self.assertFalse(verify(signature, message, public[:-1]))


if __name__ == "__main__":
    unittest.main()
