#!/usr/bin/env python3
"""Complete a MeshCore private key that was clipped when it was copied, using its public key.

A MeshCore private key is 128 hex characters: the first 64 are the secret scalar, which the public key is made from;
the last 64 only seed signatures (any value signs validly, as the same identity). So:

* characters missing from the second half are filled in at random: the identity is unchanged;
* characters missing from the first half are searched for until the public key matches. Each one more takes 16
  times longer. On a laptop: up to 5 missing, seconds; 6, about a minute; 7, about 20 minutes; 8, about 5 hours. A
  Raspberry Pi is roughly 10 times slower, so for 7 or more, run it on a faster machine of your own.

Missing characters are taken to be at the end, unless you mark where they are with "?" (one per character).

    python3 tools/recover_room_key.py --pub <public key, 64 hex> --out room.key
        asks for what you have of the private key; writes the complete key to room.key (readable only by you)

Then: python3 tools/migrate_room_to_tenant.py ... --key-file room.key   (and delete room.key afterwards)
"""
import argparse
import getpass
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "meshroom"))
import meshroom as mr

HEX = "0123456789abcdef"


def scalar_of(hex64):
    return int.from_bytes(bytes.fromhex(hex64), "little") & ((1 << 255) - 1)


def decode_point(pub):
    """The public key as an affine point (x, y), to compare against without encoding every candidate."""
    y = int.from_bytes(pub, "little") & ((1 << 255) - 1)
    sign = pub[31] >> 7
    P = mr._P
    x2 = (y * y - 1) * pow(mr._ED_D * y * y + 1, P - 2, P) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * pow(2, (P - 1) // 4, P) % P
    if (x * x - x2) % P:
        raise ValueError("that isn't a valid public key")
    if (x & 1) != sign:
        x = P - x
    return x, y


def search(first, pub, progress=True):
    """first: 64 characters of the scalar, "?" where unknown. The completed 64 characters, or None."""
    unknown = [i for i, c in enumerate(first) if c == "?"]
    base = first.replace("?", "0")
    if not unknown:
        return base if mr._ed_encode(mr._ed_mul(scalar_of(base), mr._ED_B)) == pub else None
    tx, ty = decode_point(pub)
    P = mr._P
    # hex character i is nibble (high if i is even) of byte i // 2, little-endian: worth 16^(1 - i % 2) * 256^(i // 2)
    weight = [(16 if i % 2 == 0 else 1) << (8 * (i // 2)) for i in unknown]
    step = [mr._ed_mul(w, mr._ED_B) for w in weight]                          # + one in that character
    back = [mr._ed_mul((-15 * w) % mr._ED_L, mr._ED_B) for w in weight]      # 15 -> 0 in that character
    point = mr._ed_mul(scalar_of(base) % mr._ED_L, mr._ED_B)
    digits = [0] * len(unknown)
    total, n, t0 = 16 ** len(unknown), 0, time.monotonic()
    while True:
        X, Y, Z = point[0], point[1], point[2]
        if X == tx * Z % P and Y == ty * Z % P:
            out = list(base)
            for i, d in zip(unknown, digits):
                out[i] = HEX[d]
            return "".join(out)
        n += 1
        if progress and n % 200000 == 0:
            rate = n / (time.monotonic() - t0)
            print("  %d of %d tried, about %d s to go at most" % (n, total, (total - n) / rate), flush=True)
        k = 0                                               # odometer: the last unknown character moves fastest
        while k < len(digits):
            j = len(digits) - 1 - k
            if digits[j] < 15:
                digits[j] += 1
                point = mr._ed_add(point, step[j])
                break
            digits[j] = 0
            point = mr._ed_add(point, back[j])
            k += 1
        else:
            return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pub", required=True, help="the room's public key, 64 hex characters")
    ap.add_argument("--key", help="what you have of the private key (default: asked for)")
    ap.add_argument("--out", required=True, help="file to write the complete key to")
    ap.add_argument("--max-missing", type=int, default=8, help="refuse to search for more unknown characters than this")
    a = ap.parse_args(argv)

    pub_hex = "".join(a.pub.split()).lstrip(">").lower()
    if len(pub_hex) != 64 or any(c not in HEX for c in pub_hex):
        sys.exit("--pub must be 64 hex characters (the public key)")
    pub = bytes.fromhex(pub_hex)
    have = a.key or getpass.getpass("what you have of the private key: ")
    have = "".join(have.replace("-", "").split()).lstrip(">").replace(":", "").lower()
    bad = sorted({c for c in have if c not in HEX + "?"})
    if bad:
        sys.exit("not hex: %s" % " ".join(repr(c) for c in bad))
    if len(have) > 128:
        sys.exit("that's %d characters: more than a private key's 128" % len(have))
    have = have + "?" * (128 - len(have))                   # (clipped at the end, unless marked with ?)
    first, second = have[:64], have[64:]
    missing = first.count("?")
    print("have %d of 128 characters: %d missing from the secret half, %d from the signing half" % (
        128 - have.count("?"), missing, second.count("?")))
    if missing > a.max_missing:
        sys.exit("%d characters missing from the secret half: too many to search for (16^%d tries). The key can't "
                 "be recovered from this copy." % (missing, missing))
    if missing:
        print("searching %d possibilities..." % 16 ** missing)
    found = search(first, pub)
    if found is None:
        sys.exit("no match: what you have doesn't belong to that public key (a wrong character, or missing ones "
                 "somewhere other than the end: mark those with ?)")
    second = "".join(secrets.choice(HEX) if c == "?" else c for c in second)
    key = found + second
    if mr.RepeaterIdentity(bytes.fromhex(key)).pub_key != pub:
        sys.exit("internal check failed: not writing a key")
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    print("recovered: the key matches public key %s. Written to %s (readable only by you)." % (pub_hex.upper(), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
