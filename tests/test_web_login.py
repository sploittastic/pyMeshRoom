"""MeshCore Web logins: signing with the node's private key instead of an API key. Challenges, the host check, pending
(receive-only) until the key is heard on the air or the admin allows it, what a web session may send, kick and ban,
the dashboard's endpoints, the node directory behind it, and a separate connection limit from bots."""
import json
import os
import pathlib
import struct
import sys
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "meshroom"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import meshroom as mr
from test_kaas import WebTestCase, flood
from test_virtual_companion import Harness

import meshroom_kaas as kc
from meshroom_kaas.crypto import PUBLIC_CHANNEL_KEY, encrypt_then_mac, channel_hash


def advert(ident, name, ts=None, lat=None, lon=None):
    """A signed flood advert for ident, as a MeshCore app sends it."""
    flags, body = mr.ADV_TYPE_CHAT | 0x80, b""
    if lat is not None:
        flags |= 0x10
        body = struct.pack("<ii", int(lat * 1e6), int(lon * 1e6))
    app = bytes([flags]) + body + name.encode()
    tsb = struct.pack("<I", ts or int(time.time()))
    return flood(mr.PT_ADVERT, ident.pub + tsb + ident.sign(ident.pub + tsb + app) + app)


def channel_msg(text):
    data = struct.pack("<I", int(time.time())) + b"\0" + text.encode()
    return flood(mr.PT_GRP_TXT, bytes([channel_hash(PUBLIC_CHANNEL_KEY)]) +
                 encrypt_then_mac(PUBLIC_CHANNEL_KEY + bytes(16), data))


class WebLoginTests(WebTestCase):

    def setUp(self):
        super().setUp()
        self.host = "127.0.0.1:%d" % self.port

    def post(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, method="POST", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def enable(self, **extra):
        self.http("POST", "/api/login", {"password": "adminpw"})
        self.assertEqual(self.http("POST", "/api/v1/web/settings", dict(web_login=True, **extra))[0], 200)
        self.h.pump(lambda: self.h.cfg.web_login, 5)

    def login(self, ident, name="Tester", host=None, tamper=None):
        code, d = self.post("/api/v1/web/challenge")
        self.assertEqual(code, 200, d)
        host = host or self.host
        msg = mr.web_login_message(host, d["challenge"], ident.pub.hex())
        body = dict(pub=ident.pub.hex(), signature=ident.sign(msg).hex(), challenge=d["challenge"], host=host, name=name)
        if tamper:
            tamper(body)
        return self.post("/api/v1/web/login", body)

    def connect(self, ticket):
        c = kc.KaasClient(self.url + "?ticket=" + ticket, "")
        c.ws = kc.ws.WebSocket.connect(self.url + "?ticket=" + ticket, {}, 10)
        c.hello = json.loads(c.ws.recv()[1])
        c.closed.clear()
        msgs = []
        c.on_packet(lambda p: None)
        import threading

        def reader():
            try:
                while True:
                    op, data = c.ws.recv()
                    msgs.append(json.loads(data))
            except kc.Closed as e:
                c.close_reason = e
            except OSError:
                pass
            finally:
                c.closed.set()
        threading.Thread(target=reader, daemon=True).start()
        c.msgs = msgs
        return c

    def send(self, c, raw, rid):
        c.ws.send_text(json.dumps({"type": "send", "id": rid, "raw": raw.hex()}))

        def done():
            return next((m for m in c.msgs if m.get("type") == "send_result" and m.get("id") == rid
                         and m.get("status") != "queued"), None)
        self.assertTrue(self.h.pump(lambda: done() is not None, 10), c.msgs)
        return done()

    def heard_on_air(self, ident, name):
        self.h.events.put(("rxraw", advert(ident, name)))
        self.assertTrue(self.h.pump(lambda: self.h.room.heard_on_rf(ident.pub) is not None, 10))

    # ---------------------------------------------------------------- tests

    def test_off_by_default(self):
        self.assertFalse(self.h.cfg.web_login)
        self.assertEqual(self.post("/api/v1/web/challenge")[0], 403)
        self.assertEqual(self.post("/api/v1/web/login", {})[0], 403)

    def test_challenge_signature_and_host_are_checked(self):
        self.enable()
        me = kc.Identity.generate()
        other = kc.Identity.generate()
        self.assertEqual(self.login(me, tamper=lambda b: b.update(signature=other.sign(b"x").hex()))[0], 401)
        self.assertEqual(self.login(me, tamper=lambda b: b.update(pub=other.pub.hex()))[0], 401)
        code, d = self.login(me, host="evil.example.com")
        self.assertEqual(code, 401)
        self.assertIn("evil.example.com", d["error"])
        self.assertEqual(self.login(me, tamper=lambda b: b.update(challenge="wc_made_up"))[0], 401)
        code, ch = self.post("/api/v1/web/challenge")
        sig = me.sign(mr.web_login_message(self.host, ch["challenge"], me.pub.hex())).hex()
        body = dict(pub=me.pub.hex(), signature=sig, challenge=ch["challenge"], host=self.host)
        self.assertEqual(self.post("/api/v1/web/login", body)[0], 200)
        self.assertEqual(self.post("/api/v1/web/login", body)[0], 401)         # a challenge works once
        room_id = self.h.room.id

        class RoomIdent:                                                     # (the room's own key may not log in)
            pub = room_id.pub_key
            sign = staticmethod(room_id.sign)
        self.assertEqual(self.login(RoomIdent)[0], 403)

    def test_proxy_host(self):
        """Behind a proxy on this machine the browser signs the public host name, sent as X-Forwarded-Host."""
        self.enable()
        me = kc.Identity.generate()
        code, d = self.post("/api/v1/web/challenge")
        msg = mr.web_login_message("mesh.example.com", d["challenge"], me.pub.hex())
        body = dict(pub=me.pub.hex(), signature=me.sign(msg).hex(), challenge=d["challenge"], host="mesh.example.com")
        self.assertEqual(self.post("/api/v1/web/login", body, {"X-Forwarded-Host": "mesh.example.com"})[0], 200)

    def test_pending_until_heard_then_sends_as_itself(self):
        self.enable()
        me = kc.Identity.generate()
        code, d = self.login(me, "Alice")
        self.assertEqual((code, d["state"], d["send"]), (200, "pending", False))
        c = self.connect(d["ticket"])
        self.assertEqual((c.hello["key"]["web"], c.hello["key"]["pub"], c.hello["key"]["send"]), (True, me.pub.hex(), False))
        self.h.events.put(("rxraw", channel_msg("Bob: hi")))                 # a pending user still receives
        self.assertTrue(self.h.pump(lambda: any(m.get("type") == "packet" for m in c.msgs), 10))
        r = self.send(c, channel_msg("Alice: hello"), 1)
        self.assertEqual(r["status"], "dropped")
        self.assertIn("heard", r["reason"])
        self.heard_on_air(me, "Alice")                                       # her own radio adverts: now she may send
        self.assertTrue(self.h.pump(lambda: any(m.get("type") == "access" and m["send"] for m in c.msgs), 10), c.msgs)
        self.assertEqual(self.send(c, channel_msg("Alice: hello"), 2)["status"], "sent")
        self.assertEqual(self.send(c, advert(me, "Alice"), 3)["status"], "sent")
        someone = kc.Identity.generate()
        r = self.send(c, advert(someone, "Mallory"), 4)
        self.assertEqual((r["status"], r["reason"]), ("dropped", "an advert must be for your own key"))
        dm = mr.Packet(mr.PT_TXT_MSG, bytes([0x42, someone.pub[0] ^ (0 if someone.pub[0] != me.pub[0] else 1)]) + bytes(18))
        dm.header |= mr.ROUTE_FLOOD
        r = self.send(c, dm.encode(), 5)
        self.assertEqual((r["status"], r["reason"]), ("dropped", "a message must be from your own key"))
        trace = mr.Packet(mr.PT_TRACE, bytes(12))
        trace.header |= mr.ROUTE_DIRECT
        r = self.send(c, trace.encode(), 6)
        self.assertEqual((r["status"], r["reason"]), ("dropped", "web sessions can't send trace packets"))
        rows = self.http("GET", "/api/v1/web/users")[1]["users"]
        self.assertEqual([(u["pub"], u["name"], u["advert_name"], u["send"], u["connected"]) for u in rows],
                         [(me.pub.hex(), "Alice", "Alice", True, 1)])
        self.assertGreaterEqual(rows[0]["stats"]["tx"][1], 2)
        self.assertGreaterEqual(rows[0]["stats"]["rx"][1], 1)
        c.ws.close()

    def test_an_advert_sent_through_the_api_isnt_heard_on_the_air(self):
        """Only this radio's own reception counts: a bot's API key can't make a key look heard."""
        self.enable()
        token = self.make_key()
        bot = kc.KaasClient(self.url, token)
        self.h.run_client(bot.connect)
        newbie = kc.Identity.generate()
        res = self.h.run_client(lambda: bot.send(advert(newbie, "Newbie")))
        self.assertIn(res.status, ("sent", "local"))
        self.h.pump(lambda: False, 0.5)
        self.assertIsNone(self.h.room.heard_on_rf(newbie.pub))
        self.assertEqual(self.login(newbie)[1]["state"], "pending")
        bot.close()

    def test_admin_allow_kick_ban(self):
        self.enable()
        me = kc.Identity.generate()
        c = self.connect(self.login(me, "Newbie")[1]["ticket"])
        self.assertFalse(c.hello["key"]["send"])
        self.assertEqual(self.http("POST", "/api/v1/web/users/%s/allow" % me.pub.hex(), {})[0], 200)
        self.assertTrue(self.h.pump(lambda: any(m.get("type") == "access" and m["send"] for m in c.msgs), 10))
        self.assertEqual(self.send(c, channel_msg("Newbie: thanks"), 1)["status"], "sent")
        self.http("POST", "/api/v1/web/users/%s/kick" % me.pub.hex(), {})
        self.assertTrue(self.h.pump(lambda: c.closed.is_set(), 10))
        self.assertEqual(c.close_reason.code, 4005)
        code, d = self.login(me, "Newbie")                                  # kicked, not banned: back in, still allowed
        self.assertEqual((code, d["state"]), (200, "allowed"))
        c = self.connect(d["ticket"])
        self.http("POST", "/api/v1/web/users/%s/ban" % me.pub.hex(), {})
        self.assertTrue(self.h.pump(lambda: c.closed.is_set(), 10))
        self.assertEqual(c.close_reason.code, 4003)
        self.assertEqual(self.login(me)[0], 403)
        users = self.http("GET", "/api/v1/web/users")[1]
        self.assertEqual([b["pub"] for b in users["banned"]], [me.pub.hex()])
        self.assertEqual(users["banned"][0]["name"], "Newbie")
        self.assertNotIn(me.pub.hex(), self.h.cfg.web_approved)              # a ban also takes back "allow send"
        self.http("POST", "/api/v1/web/users/%s/unban" % me.pub.hex(), {})
        self.assertTrue(self.h.pump(lambda: not self.h.cfg.web_banned, 5))
        self.assertEqual(self.login(me)[1]["state"], "pending")

    def test_turning_logins_off_disconnects(self):
        self.enable()
        c = self.connect(self.login(kc.Identity.generate())[1]["ticket"])
        self.http("POST", "/api/v1/web/settings", {"web_login": False})
        self.assertTrue(self.h.pump(lambda: c.closed.is_set(), 10))
        self.assertEqual(c.close_reason.code, 4004)

    def test_admin_endpoints_need_the_admin(self):
        self.assertEqual(self.http("GET", "/api/v1/web/users")[0], 401)
        self.assertEqual(self.http("POST", "/api/v1/web/settings", {"web_login": True})[0], 401)
        self.assertEqual(self.http("POST", "/api/v1/web/users/%s/allow" % ("ab" * 32), {})[0], 401)
        self.assertFalse(self.h.cfg.web_login)

    def test_users_sorted_by_most_recently_active(self):
        self.enable()
        a, b = kc.Identity.generate(), kc.Identity.generate()
        self.login(a, "A")
        hub = self.h.room.kaas
        hub.wusers[a.pub.hex()]["last"] -= 100
        self.login(b, "B")
        names = [u["name"] for u in self.http("GET", "/api/v1/web/users")[1]["users"]]
        self.assertEqual(names, ["B", "A"])

    def test_web_sessions_have_their_own_limit(self):
        self.enable(web_max_clients=1)
        first = self.connect(self.login(kc.Identity.generate())[1]["ticket"])
        with self.assertRaises(kc.HandshakeError) as e:
            self.connect(self.login(kc.Identity.generate())[1]["ticket"])
        self.assertEqual(e.exception.status, "503")
        bot = kc.KaasClient(self.url, self.make_key())                        # a bot still gets in
        self.assertEqual(self.h.run_client(bot.connect)["type"], "hello")
        bot.close()
        first.ws.close()


class NodeDirectoryTests(unittest.TestCase):
    def test_heard_nodes_are_kept_with_full_keys(self):
        h = Harness()
        try:
            a = kc.Identity.generate()
            h.events.put(("rxraw", advert(a, "Alice", ts=1000, lat=36.5, lon=-121.9)))
            h.pump(lambda: a.pub in h.room.nodes, 5)
            n = h.room.nodes[a.pub]
            self.assertEqual((n["type"], n["name"], n["lat"], n["lon"], n["adv_ts"]), (mr.ADV_TYPE_CHAT, "Alice", 36.5, -121.9, 1000))
            self.assertIsNotNone(n["rf"])
            h.events.put(("rxraw", advert(a, "Old name", ts=999)))         # an older advert: heard, but no rename
            h.pump(lambda: False, 0.3)
            self.assertEqual(h.room.nodes[a.pub]["name"], "Alice")
            h.room.flush_topology()
            h.room.store.close()
            st = mr.Store(os.path.join(h.cfg.data_dir, "room.db"))
            rows = st.read("SELECT pubkey, type, name, lat, lon, adv_ts FROM nodes")
            self.assertEqual([(bytes(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows],
                             [(a.pub, mr.ADV_TYPE_CHAT, "Alice", 36.5, -121.9, 1000)])
            st.close()
        finally:
            h.tmp.cleanup()

    def test_observer_reports_count_only_when_trusted(self):
        h = Harness(mqtt_advert_ingest=True)
        try:
            a = kc.Identity.generate()
            h.room.observer_advert(advert(a, "Far away"), b"")
            self.assertIsNone(h.room.heard_on_rf(a.pub))
            self.assertIsNotNone(h.room.heard_on_rf(a.pub, trust_observers=True))
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
