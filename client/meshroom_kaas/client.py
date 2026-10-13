"""The KaaS connection: every packet the radio hears and sends comes in; your raw packets go out."""
import itertools
import json
import logging
import queue
import threading

from .packet import Packet
from .ws import Closed, OP_TEXT, WebSocket

log = logging.getLogger("meshroom_kaas")

FINAL = ("sent", "local", "dropped")


class SendResult(dict):
    """{"status": "queued" | "sent" | "local" | "dropped", "reason": ..., "retry_after": ...}.
    sent: it went on the air. local: it was only for a node on the server's radio (a room there, say), which
    got it without it going on the air. dropped: refused (reason says why; retry_after, when given, is in seconds)."""

    status = property(lambda s: s.get("status"))
    ok = property(lambda s: s.get("status") in ("sent", "local"))


class KaasClient:
    """KaasClient("wss://host/api/v1/kaas", "kaas_...").

    connect() opens the websocket; a reader thread then delivers each packet to every on_packet callback (on that
    thread: keep them quick) and to packets(). send() transmits a raw packet and waits for the server's verdict."""

    def __init__(self, url, key, ssl_context=None):
        self.url, self.key, self.ssl_context = url, key, ssl_context
        self.ws = None
        self.hello = None
        self.callbacks = []
        self.waiting = {}
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.inbox = None
        self.closed = threading.Event()
        self.close_reason = None
        self.dropped = 0                    # packets the server dropped because this client fell behind

    def connect(self, timeout=10.0):
        self.ws = WebSocket.connect(self.url, {"Authorization": "Bearer " + self.key}, timeout, self.ssl_context)
        op, data = self.ws.recv()
        self.hello = json.loads(data)
        if self.hello.get("type") != "hello":
            raise RuntimeError("expected a hello from the server, got %r" % self.hello)
        self.closed.clear()
        threading.Thread(target=self._reader, name="kaas-reader", daemon=True).start()
        return self.hello

    def on_packet(self, fn):
        """fn(packet): called for every packet (packet.dir is "rx" or "tx"). Usable as a decorator."""
        self.callbacks.append(fn)
        return fn

    def packets(self, maxsize=1000):
        """Iterate over packets as they come (until the connection closes)."""
        with self.lock:
            if self.inbox is None:
                self.inbox = queue.Queue(maxsize)
        while True:
            p = self.inbox.get()
            if p is None:
                return
            yield p

    def send(self, packet, wait=True, timeout=60.0):
        """Send a raw packet (a Packet or bytes). wait=True: block until it's sent, handed to a local node or dropped
        (a SendResult); wait=False: return right away with {"status": "queued"} or None."""
        raw = packet.encode() if isinstance(packet, Packet) else bytes(packet)
        rid = next(self.ids)
        ev = threading.Event()
        box = {}
        with self.lock:
            self.waiting[rid] = (ev, box, wait)
        self.ws.send_text(json.dumps({"type": "send", "id": rid, "raw": raw.hex()}))
        if not wait:
            return None
        if not ev.wait(timeout):
            with self.lock:
                self.waiting.pop(rid, None)
            return SendResult(box.get("last") or {"status": "queued"})
        return SendResult(box["final"])

    def close(self):
        if self.ws is not None:
            self.ws.close()
        self.closed.wait(5)

    def wait(self, timeout=None):
        """Block until the connection closes. Returns its close reason."""
        self.closed.wait(timeout)
        return self.close_reason

    def _reader(self):
        try:
            while True:
                op, data = self.ws.recv()
                if op != OP_TEXT:
                    continue
                msg = json.loads(data)
                t = msg.get("type")
                if t == "packet":
                    p = Packet.parse(bytes.fromhex(msg["raw"]))
                    if p is None:
                        continue
                    p.dir, p.snr, p.rssi, p.ts = msg.get("dir"), msg.get("snr"), msg.get("rssi"), msg.get("ts")
                    for fn in list(self.callbacks):
                        try:
                            fn(p)
                        except Exception:
                            log.exception("on_packet callback")
                    if self.inbox is not None:
                        try:
                            self.inbox.put_nowait(p)
                        except queue.Full:
                            pass
                elif t == "send_result":
                    with self.lock:
                        w = self.waiting.get(msg.get("id"))
                        if w and msg.get("status") in FINAL:
                            del self.waiting[msg.get("id")]
                    if w:
                        w[1]["last"] = msg
                        if msg.get("status") in FINAL:
                            w[1]["final"] = msg
                            w[0].set()
                elif t == "dropped":
                    self.dropped += msg.get("count", 0)
                    log.warning("the server dropped %s packets: this client is reading too slowly", msg.get("count"))
                elif t == "error":
                    log.warning("server: %s", msg.get("error"))
        except Closed as e:
            self.close_reason = e
        except Exception as e:
            log.exception("reader")
            self.close_reason = e
        finally:
            with self.lock:
                waiting, self.waiting = self.waiting, {}
            for ev, box, _ in waiting.values():
                box["final"] = {"status": "dropped", "reason": "connection closed"}
                ev.set()
            if self.inbox is not None:
                try:
                    self.inbox.put_nowait(None)
                except queue.Full:
                    pass
            self.closed.set()
