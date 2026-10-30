"""Airtime: one ceiling for everything the radio sends, a guaranteed share of it for each class of traffic, and
borrowing what the others leave unused (meshroom.Airtime)."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "meshroom"))
import meshroom as mr

PKT_MS = 400


def config(**kw):
    c = dict(tx_duty_pct=50, tx_share_control_pct=10, tx_share_relay_pct=15, tx_share_room_pct=10,
             tx_share_users_pct=10, tx_share_background_pct=5)
    c.update(kw)
    return SimpleNamespace(**c)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class AirtimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        p = mock.patch.object(mr.time, "monotonic", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def run_air(self, air, busy, seconds):
        """A radio that sends a 400 ms packet whenever one of the busy classes may (in the room's order), else idles
        a tenth of a second: the airtime each class got, as a share of the time."""
        got = dict.fromkeys(mr.TX_CLASSES, 0.0)
        end = self.clock.t + seconds
        while self.clock.t < end:
            cls = next((c for c in busy if air.can(c, PKT_MS)), None)
            if cls is None:
                self.clock.t += 0.1
                continue
            air.charge(cls, PKT_MS)
            got[cls] += PKT_MS
            self.clock.t += PKT_MS / 1000.0
        return {k: v / 1000.0 / seconds for k, v in got.items()}

    def test_alone_a_class_may_use_the_whole_ceiling(self):
        got = self.run_air(mr.Airtime(config()), ["room"], 3000)
        self.assertGreater(got["room"], 0.48)
        self.assertLess(got["room"], 0.52)

    def test_when_all_are_busy_each_gets_its_share(self):
        air = mr.Airtime(config())
        got = self.run_air(air, ["control", "relay", "room", "users", "background"], 3000)
        self.assertLess(sum(got.values()), 0.53)
        for k, share in air.rates()[1].items():
            self.assertGreater(got[k], share * 0.9, k)

    def test_a_guarantee_holds_against_a_busy_borrower(self):
        """Relays keep the channel busy; users who start later still get their 10%."""
        air = mr.Airtime(config())
        self.run_air(air, ["relay"], 600)
        got = self.run_air(air, ["relay", "users"], 3000)              # (relays go first, as in the room)
        self.assertGreater(got["users"], 0.095)
        self.assertLess(got["relay"] + got["users"], 0.53)

    def test_control_may_go_briefly_over(self):
        """ACKs are useless late: with the ceiling spent they still go, for a burst; then they wait too."""
        air = mr.Airtime(config())
        air.refill()
        air.pool, air.own["control"], air.own["room"] = 0.0, 0.0, 0.0
        self.assertTrue(air.can("control", PKT_MS))
        self.assertFalse(air.can("room", PKT_MS))
        n = 0
        while air.can("control", PKT_MS):
            air.charge("control", PKT_MS)
            n += 1
        self.assertEqual(n, mr.TX_CONTROL_OVER_MS // PKT_MS)
        got = self.run_air(air, ["control"], 3000)                      # kept up: no more than the ceiling
        self.assertLess(got["control"], 0.51)

    def test_shares_beyond_the_ceiling_are_scaled_to_fit(self):
        ceiling, shares = mr.Airtime(config(tx_duty_pct=20)).rates()
        self.assertAlmostEqual(ceiling, 0.2)
        self.assertAlmostEqual(sum(shares.values()), 0.2)
        self.assertAlmostEqual(shares["relay"], 0.06)

    def test_a_share_of_zero_only_borrows(self):
        air = mr.Airtime(config(tx_share_users_pct=0))
        got = self.run_air(air, ["users"], 1000)
        self.assertGreater(got["users"], 0.45)
        got = self.run_air(air, ["relay", "users"], 2000)
        self.assertLess(got["users"], 0.05)

    def test_state(self):
        air = mr.Airtime(config())
        self.run_air(air, ["room"], 120)
        st = air.state()
        self.assertEqual(st["ceiling_pct"], 50.0)
        self.assertEqual(st["share_pct"]["relay"], 15.0)
        self.assertGreater(st["used_pct"]["room"], 40)
        self.assertEqual(st["total_pct"], st["used_pct"]["room"])

    def test_classes(self):
        p = mr.Packet(mr.PT_TXT_MSG, b"x" * 20)
        self.assertEqual(mr.tx_class(p, False, True, None, None), "relay")
        self.assertEqual(mr.tx_class(p, True, False, object(), ("conn", 1)), "users")
        self.assertEqual(mr.tx_class(p, False, False, "vc", None), "users")
        self.assertEqual(mr.tx_class(mr.Packet(mr.PT_ADVERT, b"x"), False, False, None, None), "background")
        self.assertEqual(mr.tx_class(p, True, False, None, None), "room")
        self.assertEqual(mr.tx_class(mr.Packet(mr.PT_ACK, b"x" * 4), False, False, None, None), "control")

    def test_settings(self):
        self.assertEqual(mr.airtime_changes({"duty_pct": 40, "share_pct": {"relay": 20}}),
                         {"tx_duty_pct": 40, "tx_share_relay_pct": 20})
        for bad in ({"duty_pct": 0}, {"duty_pct": 101}, {"share_pct": {"relay": -1}}, {"share_pct": {"bots": 5}},
                    {"duty_pct": "lots"}):
            with self.assertRaises(ValueError):
                mr.airtime_changes(bad)


if __name__ == "__main__":
    unittest.main()
