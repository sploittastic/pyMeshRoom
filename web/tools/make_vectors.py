#!/usr/bin/env python3
"""Writes test/vectors.json from pyMeshRoom's Python client (the reference): python3 web/tools/make_vectors.py"""
import hashlib
import hmac
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "client"))
from meshroom_kaas import Identity, Packet, channel_hash, hashtag_channel_key  # noqa: E402
from meshroom_kaas import crypto  # noqa: E402
from meshroom_kaas.packet import PT_ADVERT, PT_GRP_TXT  # noqa: E402

seed = bytes(range(32))
ident = Identity(seed)
ident2 = Identity(hashlib.sha256(b"second").digest() * 2)       # a 64-byte key as given (not from a seed)
msgs = [b"", b"abc", bytes(range(200))]
out = {
    "sha256": [[m.hex(), hashlib.sha256(m).hexdigest()] for m in msgs],
    "sha512": [[m.hex(), hashlib.sha512(m).hexdigest()] for m in msgs],
    "hmac": [[k.hex(), m.hex(), hmac.new(k, m, hashlib.sha256).hexdigest()] for k in (bytes(32), b"key" * 20) for m in msgs],
    "identities": [],
    "channels": [],
}
for i in (ident, ident2):
    msg = b"meshcore advert body"
    out["identities"].append({"private": i.private_key.hex(), "public": i.pub.hex(), "msg": msg.hex(),
                              "sig": i.sign(msg).hex()})
out["identities"][0]["seed"] = seed.hex()
for name, key in (("Public", crypto.PUBLIC_CHANNEL_KEY), ("#test", hashtag_channel_key("#test")),
                  ("Ops", bytes.fromhex("00112233445566778899aabbccddeeff"))):
    data = struct.pack("<I", 1760000000) + b"\0" + "Alice: héllo wörld".encode()
    payload = bytes([channel_hash(key)]) + crypto.encrypt_then_mac(key + bytes(16), data)
    pkt = Packet.flood(PT_GRP_TXT, payload)
    out["channels"].append({"name": name, "key": key.hex(), "hash": channel_hash(key), "plain": data.hex(),
                            "raw": pkt.encode().hex(), "packet_hash": pkt.packet_hash().hex()})
app = bytes([0x81]) + b"Alice"
ts = struct.pack("<I", 1760000000)
adv = Packet.flood(PT_ADVERT, ident.pub + ts + ident.sign(ident.pub + ts + app) + app)
out["advert"] = {"private": ident.private_key.hex(), "name": "Alice", "ts": 1760000000, "raw": adv.encode().hex()}
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "vectors.json")
with open(path, "w") as f:
    json.dump(out, f, indent=1)
print("wrote", path)
