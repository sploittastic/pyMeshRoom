"""tools/recover_room_key.py: a private key clipped when copied, completed from its public key."""
import hashlib
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "meshroom"))
sys.path.insert(0, str(ROOT / "tools"))
import meshroom as mr
import recover_room_key as rk


def firmware_key(seed):
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248; h[31] &= 127; h[31] |= 64
    return bytes(h)


class RecoverTests(unittest.TestCase):
    def setUp(self):
        self.key = firmware_key(b"clipped room")
        self.pub = mr.RepeaterIdentity(self.key).pub_key
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def run_tool(self, have, name):
        out = os.path.join(self.d.name, name)
        rk.main(["--pub", self.pub.hex(), "--key", have, "--out", out])
        with open(out) as f:
            got = f.read().strip()
        self.assertEqual(oct(os.stat(out).st_mode & 0o777), "0o600")
        return got

    def test_clipped_in_the_signing_half(self):
        got = self.run_tool("> " + self.key.hex()[:100], "a")
        self.assertEqual(got[:100], self.key.hex()[:100])
        self.assertEqual(mr.RepeaterIdentity(bytes.fromhex(got)).pub_key, self.pub)

    def test_clipped_into_the_secret_half(self):
        got = self.run_tool(self.key.hex()[:61], "b")                       # 3 characters of the scalar gone
        self.assertEqual(got[:64], self.key.hex()[:64])

    def test_missing_in_the_middle_marked(self):
        h = self.key.hex()
        got = self.run_tool(h[:10] + "??" + h[12:], "c")
        self.assertEqual(got, h)

    def test_a_key_of_someone_else(self):
        with self.assertRaises(SystemExit) as e:
            self.run_tool(firmware_key(b"other").hex()[:62], "d")
        self.assertIn("no match", str(e.exception))

    def test_signs_as_the_room(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        got = self.run_tool(self.key.hex()[:70], "e")
        Ed25519PublicKey.from_public_bytes(self.pub).verify(mr.RepeaterIdentity(bytes.fromhex(got)).sign(b"hi"), b"hi")


if __name__ == "__main__":
    unittest.main()
