"""A pyMeshRoom room on a fake radio, its dashboard on PORT (admin password "adminpw", web logins on), a KaaS key
printed, and the main loop pumped forever.
Everything the radio transmits is printed as "TX <hex>"; lines "RX <hex>" appended to WORK/rx.txt are heard.

    python3 devserver.py PYMESHROOM_CHECKOUT PORT WORK_DIR"""
import os, sys, threading, time
REPO = sys.argv[1]
sys.path.insert(0, os.path.join(REPO, "meshroom")); sys.path.insert(0, os.path.join(REPO, "tests"))
import meshroom as mr
from test_virtual_companion import Harness
port, work = int(sys.argv[2]), sys.argv[3]
h = Harness(web_password="adminpw", room_password="hello", kaas_send_window_s=2, push_quiet_ms=0)
h.cfg.set("web_port", port); h.cfg.set("web_bind", "127.0.0.1"); h.cfg.set("web_login", True)
web = mr.WebUI(h.room, h.events)
token, entry = h.room.kaas.new_key(dict(receive=True, send=True, description="dev", expires=None))
h.events.put(("kaas_key_add", entry))
h.pump(lambda: h.room.kaas.find(token) is not None, 5)
print("TOKEN", token, flush=True)
def reader():
    rx = os.path.join(work, "rx.txt")
    open(rx, "w").close()
    done = 0
    while True:
        lines = open(rx).read().splitlines()
        for line in lines[done:]:
            if line.startswith("RX "):
                h.events.put(("rxraw", bytes.fromhex(line[3:].strip())))
        done = len(lines)
        time.sleep(0.1)
threading.Thread(target=reader, daemon=True).start()
seen = 0
while True:
    h.pump(lambda: False, 0.2)
    sent = getattr(h.modem, "sent", None)
    if sent is not None:
        while seen < len(sent):
            print("TX", bytes(sent[seen]).hex(), flush=True)
            seen += 1
