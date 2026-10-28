"""Packets as the radio would hear them, made by pyMeshRoom's Python client:
    mkpkt.py CHANNEL SENDER TEXT        a channel message (CHANNEL: Public, #hashtag or a private secret in hex)
    mkpkt.py --advert PRIVATE_KEY NAME  a flood advert, signed with that key"""
import os, struct, sys, time
sys.path.insert(0, os.path.join(os.environ["PYMESHROOM"], "client"))
from meshroom_kaas import Identity, Packet, crypto, hashtag_channel_key, channel_hash
from meshroom_kaas.packet import PT_ADVERT, PT_GRP_TXT
if sys.argv[1] == "--advert":
    ident, name = Identity(sys.argv[2]), sys.argv[3]
    app = bytes([0x81]) + name.encode()
    ts = struct.pack("<I", int(time.time()))
    p = Packet.flood(PT_ADVERT, ident.pub + ts + ident.sign(ident.pub + ts + app) + app)
else:
    name, sender, text = sys.argv[1], sys.argv[2], sys.argv[3]
    key = crypto.PUBLIC_CHANNEL_KEY if name == "Public" else hashtag_channel_key(name) if name.startswith("#") else bytes.fromhex(name)
    data = struct.pack("<I", int(time.time())) + b"\0" + (sender + ": " + text).encode()
    p = Packet.flood(PT_GRP_TXT, bytes([channel_hash(key)]) + crypto.encrypt_then_mac(key + bytes(16), data))
p.path_len = 2; p.path = b"\x44\x55"
print(p.encode().hex())
