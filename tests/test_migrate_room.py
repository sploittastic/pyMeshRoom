"""tools/migrate_room_to_tenant.py: the main room becomes a tenant room under its old key, keeping its members."""
import hashlib
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "meshroom"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import meshroom as mr
import migrate_room_to_tenant as mig
from test_virtual_companion import Harness


def firmware_key(seed):
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248; h[31] &= 127; h[31] |= 64
    return bytes(h)


class MigrateTests(unittest.TestCase):
    def test_the_room_moves_with_its_members(self):
        with tempfile.TemporaryDirectory() as d:
            key = firmware_key(b"old room")
            pub = mr.RepeaterIdentity(key).pub_key
            h = Harness(data_dir=d, name="Surf Room", room_password="join", admin_password="boss", max_posts=50)
            member, secret = os.urandom(32), os.urandom(32)
            h.store.execmany([
                ("INSERT INTO members (pubkey, perms, last_timestamp, last_activity, sync_since, secret) VALUES (?,?,?,?,?,?)",
                 [(member, 1, 1700000000, 1700000000, 1700000000, secret)]),
                ("INSERT INTO posts (ts, author, text, to_key) VALUES (?,?,?,?)", [(1700000001, member[:4], "hello", None)]),
                ("INSERT INTO bans (pubkey, name, ts) VALUES (?,?,?)", [(os.urandom(32), "troll", 1700000002)])])
            h.close()
            cfg_path = os.path.join(d, "meshroom.json")
            argv = ["--config", cfg_path, "--path", "surf", "--key", key.hex(), "--web-password", "roompage1"]
            with self.assertRaises(SystemExit):                                  # a key that isn't the room's
                mig.main(argv + ["--expect-pub", "AB" * 32])
            self.assertEqual(mig.main(argv + ["--dry-run"]), 0)
            self.assertFalse(os.path.exists(os.path.join(d, "tenants")))
            self.assertEqual(mig.main(argv + ["--expect-pub", pub.hex()]), 0)
            self.assertTrue(any(n.startswith("backup-before-migration-") for n in os.listdir(d)))
            with self.assertRaises(SystemExit):                                  # (only once)
                mig.main(argv)

            h = Harness(data_dir=d)
            try:
                t = h.room.tenants[pub]
                self.assertEqual((t.cfg.name, t.cfg.path, t.cfg.room_password, t.cfg.admin_password, t.cfg.max_posts),
                                 ("Surf Room", "surf", "join", "boss", 50))
                self.assertTrue(mr.check_web_password("roompage1", t.cfg.web_password_hash))
                self.assertEqual(t.members[member].secret, secret)              # no new login needed
                self.assertEqual([p[2] for p in t.posts], ["hello"])
                self.assertEqual(len(t.bans), 1)
                self.assertEqual(h.room.members, {})                             # the main room: an empty placeholder
                self.assertEqual(h.cfg.name, "pyMeshRoom host")
                self.assertNotEqual(h.cfg.room_password, "join")
            finally:
                h.close()


if __name__ == "__main__":
    unittest.main()
