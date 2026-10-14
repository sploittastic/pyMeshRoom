"""WhereBot (bots/where_bot): parsing !where, fitting replies into a channel message, and a real lookup end to end."""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bots" / "where_bot"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import where_bot as wb
except ImportError:                                        # needs pyyaml
    wb = None
from test_kaas import WebTestCase, kc

NEAR = [{"id": "44c3", "name": "Seaside", "share": 0.62}, {"id": "55b2", "name": None, "share": 0.25},
        {"direct": True, "id": "9a1f", "name": "pyMeshRoom Virtual Repeater", "share": 0.13}]
BUDGET = 160 - len("WhereBot: ")


@unittest.skipIf(wb is None, "needs pyyaml")
class ReplyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(wb.parse("!where @[platinum]"), ("mention", "platinum"))
        self.assertEqual(wb.parse("!WHERE @[Jeff T 2]"), ("mention", "Jeff T 2"))
        self.assertEqual(wb.parse("!where A1B2C3D4E5F60718"), ("key", "a1b2c3d4e5f60718"))
        self.assertEqual(wb.parse("!where platinum"), ("name", "platinum"))
        self.assertEqual(wb.parse("!where")[0], "bad")
        self.assertIsNone(wb.parse("where is everyone"))
        self.assertIsNone(wb.parse("!whereabouts"))

    def test_answers(self):
        self.assertEqual(wb.answer("mention", "platinum", {"name": "platinum", "repeaters": NEAR}, BUDGET),
                         "platinum: recently active near:\n44c3 Seaside 62%\n55b2 25%\n9a1f pyMeshRoom Virtual Repeater 13%")
        self.assertEqual(wb.answer("key", "a1b2c3d4e5f60718", {"name": "platinum", "repeaters": NEAR[:1]}, BUDGET),
                         "a1b2c3d4 (aka platinum): recently active near:\n44c3 Seaside 62%")
        self.assertEqual(wb.answer("key", "a1b2c3d4e5f60718", {"name": None, "repeaters": NEAR[:1]}, BUDGET),
                         "a1b2c3d4: recently active near:\n44c3 Seaside 62%")
        self.assertEqual(wb.answer("key", "a1b2c3d4e5f60718", {"name": None, "repeaters": []}, BUDGET), wb.UNKNOWN_KEY)
        self.assertIn("hasn't been heard", wb.answer("mention", "platinum", {"name": "platinum", "repeaters": []}, BUDGET))

    def test_long_names_fit(self):
        rpts = [{"id": "%02x%02x" % (i, i), "name": "A Very Long Repeater Name Number %d" % i, "share": s}
                for i, s in enumerate((0.4, 0.3, 0.2, 0.1))]
        who = "Somebody With A Thirty Char Nm"
        text = wb.answer("mention", who, {"name": who, "repeaters": rpts}, BUDGET)
        self.assertLessEqual(len(text.encode()), BUDGET)
        self.assertEqual(text.split("\n")[0], who + ": recently active near:")
        lines = text.split("\n")[1:]
        self.assertEqual([x.split()[0] for x in lines], ["0000", "0101", "0202", "0303"], "every repeater stays")
        self.assertTrue(all("~" in x for x in lines), "names are cut")
        self.assertEqual(wb.answer("mention", who, {"name": who, "repeaters": rpts}, 95).split("\n")[1:],
                         ["0000 40%", "0101 30%", "0202 20%", "0303 10%"], "then names go, the ids stay")
        self.assertEqual(wb.answer("mention", who, {"name": who, "repeaters": rpts}, 75).split("\n")[1:],
                         ["0000 40%", "0101 30%"], "then the least heard repeaters go")


@unittest.skipIf(wb is None, "needs pyyaml")
class WhereBotEndToEnd(WebTestCase):
    def test_where_on_a_hashtag_channel(self):
        room = self.h.room
        node = os.urandom(32)
        for _ in range(3):
            room.observe_near(node, bytes.fromhex("44c3"))
        room.learn_name(node, 10, "platinum")
        self.http("POST", "/api/login", {"password": "adminpw"})
        token = self.make_key()
        bot_c, user_c = kc.KaasClient(self.url, token), kc.KaasClient(self.url, token)
        self.h.run_client(bot_c.connect)
        self.h.run_client(user_c.connect)
        bot = kc.Node(bot_c, kc.Identity.generate(), "WhereBot")
        user = kc.Node(user_c, kc.Identity.generate(), "Jeff")
        bot_ch = bot.add_channel("#where")
        bot.on_channel(lambda ch, sender, text, ts: wb.handle(bot_c, bot, ch, sender, text))
        heard = []
        user.on_channel(lambda ch, sender, text, ts: heard.append((sender, text)))
        ch = user.add_channel("#where")
        room.observe_near(node, b"")
        here = room.id.pub_key[:2].hex()                    # no virtual repeater here: heard directly by the room
        for ask, want in (("!where @[Platinum]", "platinum: recently active near:\n44c3 75%%\n%s %s 25%%" % (here, room.cfg.name)),
                          ("!where " + node.hex(),
                           "%s (aka platinum): recently active near:\n44c3 75%%\n%s %s 25%%" % (node[:4].hex(), here, room.cfg.name)),
                          ("!where @[nobody]", wb.UNKNOWN_NAME),
                          ("!where " + os.urandom(8).hex(), wb.UNKNOWN_KEY)):
            heard.clear()
            self.h.run_client(lambda: user.send_channel(ch, ask))
            self.assertTrue(self.h.pump(lambda: heard, 15), ask)
            self.assertEqual(heard, [("WhereBot", want)])
        self.assertTrue(bot_ch)
        bot_c.close()
        user_c.close()


if __name__ == "__main__":
    unittest.main()
