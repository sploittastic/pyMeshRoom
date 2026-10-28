"""Hardening from the 2026-10-28 security review: the dashboard never builds script from names, a websocket upgrade
that fails leaves no session behind, web logins are only for this server's own names, and the HTTP service keeps
answering through slow, huge or wrong requests."""
import json
import pathlib
import socket
import struct
import sys
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "meshroom"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import meshroom as mr
from test_web_login import WebHelpers

import meshroom_kaas as kc


class DashboardTests(unittest.TestCase):
    def test_no_names_inside_inline_script(self):
        """A member's name (from their own advert) is data, never part of an onclick's JavaScript."""
        import re
        handlers = re.findall(r'on[a-z]+="([^"]*)"', mr.DASHBOARD_HTML)
        self.assertTrue(handlers)
        self.assertEqual([h for h in handlers if "esc(" in h or ".name" in h], [])

    def test_escaping_covers_quotes(self):
        i = mr.DASHBOARD_HTML.index("esc=s=>")
        self.assertIn("&#39;", mr.DASHBOARD_HTML[i:i + 200])
        self.assertIn("&#96;", mr.DASHBOARD_HTML[i:i + 200])


class HardeningTests(WebHelpers):
    def raw(self, data, linger=False, read=True):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        s.sendall(data)
        if linger:                                                    # a reset: no FIN, nothing read
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()
            return None
        head = s.recv(200).decode(errors="replace") if read else ""
        s.close()
        return head

    def get_ok(self):
        with urllib.request.urlopen(self.base + "/api/state", timeout=10) as r:
            return r.status == 200

    def test_dashboard_headers(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            csp, xfo = r.headers.get("Content-Security-Policy", ""), r.headers.get("X-Frame-Options")
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(xfo, "DENY")

    def test_an_upgrade_reset_midway_leaves_no_session(self):
        """Resetting the connection while the server writes its 101 used to keep the session's slot until a restart."""
        self.enable(web_max_clients=4)
        for _ in range(40):
            t = self.login(kc.Identity.generate())[1]["ticket"]
            self.raw(("GET /api/v1/kaas?ticket=%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
                      % (t, self.host)).encode(), linger=True)
        hub = self.h.room.kaas
        self.assertTrue(self.h.pump(lambda: not any(c.web for c in hub.conns), 10), len(hub.conns))
        c = self.connect(self.login(kc.Identity.generate())[1]["ticket"])           # a real user still gets in
        self.assertEqual(c.hello["type"], "hello")
        c.ws.close()

    def test_logins_only_for_this_servers_names(self):
        """A login signed for another name is refused, even with that name as Host (a relay); the admin can allow a
        name the server really goes by, from the refused list."""
        self.enable()
        me = kc.Identity.generate()

        def login_as(host, headers=None):
            code, d = self.post("/api/v1/web/challenge")
            msg = mr.web_login_message(host, d["challenge"], me.pub.hex())
            body = dict(pub=me.pub.hex(), signature=me.sign(msg).hex(), challenge=d["challenge"], host=host)
            return self.post("/api/v1/web/login", body, headers)
        self.assertEqual(login_as(self.host)[0], 200)                               # its own ip:port
        self.assertEqual(login_as("evil.example.net", {"Host": "evil.example.net"})[0], 401)
        self.assertEqual(login_as("mesh.example.com", {"X-Forwarded-Host": "mesh.example.com"})[0], 401)
        users = self.http("GET", "/api/v1/web/users")[1]
        self.assertEqual({r["host"] for r in users["refused_hosts"]}, {"evil.example.net", "mesh.example.com"})
        self.assertEqual(self.http("POST", "/api/v1/web/settings", {"web_hosts": ["Mesh.Example.com"]})[0], 200)
        self.h.pump(lambda: self.h.cfg.web_hosts == ["mesh.example.com"], 5)
        self.assertEqual(login_as("mesh.example.com")[0], 200)
        self.assertEqual(self.http("POST", "/api/v1/web/settings", {"web_hosts": ["<script>"]})[0], 400)

    def test_huge_and_slow_bodies(self):
        """A huge body is refused unread; bodies that never arrive hold their own threads, not the request slots."""
        line = "POST /api/v1/web/challenge HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n" % self.host
        self.assertIn(" 413 ", self.raw((line + "Content-Length: 1000000000\r\n\r\n").encode()))
        hung = []
        for _ in range(20):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            s.sendall((line + "Content-Length: 100\r\n\r\n").encode())       # ...and the 100 bytes never come
            hung.append(s)
        time.sleep(0.3)
        self.assertTrue(self.get_ok())
        for s in hung:
            s.close()

    def test_wrong_passwords_dont_turn_everyone_away(self):
        """The one-second brake on a wrong password no longer holds a request slot."""
        def wrong():
            req = urllib.request.Request(self.base + "/api/login", data=b'{"password":"nope"}', method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError:
                pass
        ts = [threading.Thread(target=wrong) for _ in range(24)]
        for t in ts:
            t.start()
        time.sleep(0.4)                                               # (all of them inside their second)
        self.assertTrue(self.get_ok())
        for t in ts:
            t.join()


if __name__ == "__main__":
    unittest.main()
