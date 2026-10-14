#!/usr/bin/env python3
"""WhereBot: answers "!where @[name]" or "!where <public key>" on hashtag channels with the repeaters that usually
hear that node, from the room's KaaS API.

    pip install cryptography pyyaml
    python3 where_bot.py where_bot.yaml
"""
import logging
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "client"))
from meshroom_kaas import Identity, KaasClient, Node, RequestError  # noqa: E402

log = logging.getLogger("where_bot")

MAX_TEXT_LEN = 160          # a channel message, "BotName: " included
NAME_CUTS = (None, 20, 16, 12, 8, 0)   # if a reply is too long, repeater names are cut to these lengths in turn
MIN_SHARE = 0.05            # repeaters heard less than this are left out
KEY_SHOWN = 8               # hex characters of a public key in replies

UNKNOWN_KEY = "I am not familiar with that public key"
UNKNOWN_NAME = "I am not familiar with that name. You can try lookup by public key"
SHORT_KEY = "Give at least the first 16 hex characters of the public key"
USAGE = "Usage: !where @[name] or !where <public key>"


def parse(text):
    """The node asked about in "!where ...": ("mention", name), ("name", plain text), ("key", hex), ("bad", reply),
    or None if the text isn't a !where."""
    m = re.match(r"\s*!where\b\s*(.*)$", text, re.I | re.S)
    if not m:
        return None
    arg = m.group(1).strip()
    mention = re.fullmatch(r"@\[(.+)\]", arg, re.S)
    if mention:
        return "mention", mention.group(1).strip()
    if re.fullmatch(r"[0-9a-fA-F]{16,64}", arg) and len(arg) % 2 == 0:
        return "key", arg.lower()
    return ("name", arg) if arg else ("bad", USAGE)


def fit(text, limit):
    """At most limit bytes of UTF-8."""
    b = text.encode()[:limit]
    return b.decode(errors="ignore")


def describe(rpt, cut=None):
    """ "44c3 Seaside 62%": the repeater's 2-byte id, its name (cut to cut characters; 0 leaves it out) and share."""
    rid = rpt.get("id") or "direct"         # (a server too old to name what hears a node directly)
    name = rpt.get("name") or ""
    if cut is not None and len(name) > cut:
        name = name[:cut - 1].rstrip() + "~" if cut > 1 else ""
    return " ".join(x for x in (rid, name, "%d%%" % round(rpt["share"] * 100)) if x)


def answer(kind, asked, result, budget):
    """The reply to a !where, at most budget bytes. result: KaasClient.lookup()'s answer."""
    if kind == "key":
        who = asked[:KEY_SHOWN]
        if result.get("name"):
            who += " (aka %s)" % result["name"]
    else:
        who = result.get("name") or asked
    rpts = [r for r in result["repeaters"] if r["share"] >= MIN_SHARE] or result["repeaters"][:1]
    if not rpts:
        if kind == "key" and not result.get("name"):
            return UNKNOWN_KEY
        return fit("%s hasn't been heard through a repeater lately" % who, budget)
    head = "%s: recently active near:" % who
    while True:
        for cut in NAME_CUTS:               # shorten the repeater names first, then leave out the least heard
            text = "\n".join([head] + [describe(r, cut) for r in rpts])
            if len(text.encode()) <= budget:
                return text
        if len(rpts) == 1:
            return fit(text, budget)
        rpts = rpts[:-1]                    # they come most heard first


def handle(client, node, channel, sender, text):
    if sender == node.name:
        return
    q = parse(text)
    if q is None:
        return
    kind, arg = q
    budget = MAX_TEXT_LEN - len((node.name + ": ").encode())
    if kind == "bad":
        reply = arg
    else:
        try:
            reply = answer(kind, arg, client.lookup(arg), budget)
        except RequestError as e:
            if kind != "key" and "no node named" in str(e):
                reply = SHORT_KEY if kind == "name" and re.fullmatch(r"[0-9a-fA-F]+", arg) else UNKNOWN_NAME
            else:
                log.warning("lookup %r failed: %s", arg, e)
                reply = "Sorry, I couldn't look that up just now"
    log.info("%s %s: %s -> %s", channel.name, sender, text, reply)
    node.send_channel(channel, reply)


def run(cfg):
    ident = Identity(cfg["private_key"])
    while True:
        client = KaasClient(cfg["api_endpoint"], cfg["api_key"])
        try:
            client.connect()
        except Exception as e:
            log.warning("can't connect to %s: %s", cfg["api_endpoint"], e)
            time.sleep(30)
            continue
        node = Node(client, ident, cfg["name"])
        channels = [node.add_channel(c if c.startswith("#") else "#" + c) for c in cfg["channels"]]
        log.info("connected as %s (%s), listening on %s", cfg["name"], ident.pub[:4].hex(),
                 ", ".join(c.name for c in channels))
        node.on_channel(lambda ch, sender, text, ts: handle(client, node, ch, sender, text))
        log.info("connection closed: %s", client.wait())
        time.sleep(10)


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    missing = [k for k in ("name", "private_key", "api_endpoint", "api_key", "channels") if not cfg.get(k)]
    if missing:
        raise SystemExit("%s: missing %s" % (path, ", ".join(missing)))
    if isinstance(cfg["channels"], str):
        cfg["channels"] = [cfg["channels"]]
    return cfg


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(load_config(sys.argv[1] if len(sys.argv) > 1 else "where_bot.yaml"))
