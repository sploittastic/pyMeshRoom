#!/usr/bin/env python3
"""Move the main room to a tenant room, under its old private key.

For a room whose identity was the KISS modem's: once its key has been read off the radio (repeater firmware's
serial "get prv.key") and the modem has a new key, this keeps the room's identity, members (with their saved
secrets, so nobody has to log in again), posts, routes and bans, as a tenant room. The main room is left empty, as
a placeholder on the modem's new key: renamed, with a random join password, until pyMeshRoom can run without one.

Stop pyMeshRoom first. Backups of the config and room.db are made before anything changes.

    python3 tools/migrate_room_to_tenant.py --config meshroom.json --path myroom
        asks for the private key (128 hex characters) and a web UI password for the room's own page

Options: --name (default: the main room's name), --expect-pub (the room's public key, as "get public.key" printed
it: checked against the key), --main-name, --keep-main (copy, don't empty the main room), --dry-run.
"""
import argparse
import datetime
import getpass
import json
import os
import secrets
import shutil
import socket
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "meshroom"))
import meshroom as mr

ROOM_TABLES = ("members", "posts", "routes", "bans", "member_inroutes")   # the room's own; the map stays the host's
SETTINGS = ("allow_read_only", "welcome_new_members", "welcome_message", "welcome_advert_hint", "kick_message",
            "ban_message", "advert_interval_min", "flood_advert_interval_h", "max_posts", "catchup_max")


def columns(table):
    return [c.split()[0] for c in mr.Store.TABLES[table] if not c.startswith("PRIMARY KEY")]


def running(cfg):
    """Is pyMeshRoom running on this config (its dashboard port answers)?"""
    try:
        socket.create_connection(("127.0.0.1", int(cfg.web_port)), timeout=1).close()
        return True
    except (OSError, ValueError, TypeError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="meshroom.json")
    ap.add_argument("--path", required=True, help="the tenant room's page: /<path>/")
    ap.add_argument("--name", help="the room's name (default: the main room's)")
    ap.add_argument("--key", help="the private key, hex (default: asked for, so it isn't kept in shell history)")
    ap.add_argument("--expect-pub", help="the room's public key, hex: the private key must match it")
    ap.add_argument("--web-password", help="the room page's web UI password, 6+ characters (default: asked for)")
    ap.add_argument("--main-name", default="pyMeshRoom host", help="the emptied main room's new name")
    ap.add_argument("--keep-main", action="store_true", help="leave the main room's members and posts where they are")
    ap.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    a = ap.parse_args(argv)

    cfg = mr.Config(a.config)
    data = cfg.data_dir                                     # (as pyMeshRoom reads it: relative to where it's started)
    if not os.path.isdir(data):
        data = os.path.join(os.path.dirname(os.path.abspath(a.config)), cfg.data_dir)
    room_db = os.path.join(data, "room.db")
    if not os.path.exists(room_db):
        sys.exit("no room database at %s (check data_dir in %s)" % (room_db, a.config))
    if running(cfg):
        sys.exit("pyMeshRoom seems to be running (port %s answers): stop it first" % cfg.web_port)

    key = (a.key or getpass.getpass("private key (128 hex characters): ")).strip()
    web_pw = a.web_password or getpass.getpass("web UI password for the room's own page (6+ characters): ")
    taken_paths = {str(e.get("path", "")).lower() for e in (cfg.tenant_rooms or []) if isinstance(e, dict)}
    taken_keys = {bytes.fromhex(str(e["public_key"])) for e in (cfg.tenant_rooms or []) if isinstance(e, dict) and e.get("public_key")}
    try:
        spec = mr.tenant_spec(dict(name=a.name or cfg.name, path=a.path, private_key=key, room_password=cfg.room_password,
                                   admin_password=cfg.admin_password, web_password=web_pw, lat=cfg.lat, lon=cfg.lon),
                              taken_paths, taken_keys)
    except ValueError as e:
        sys.exit("can't migrate: %s" % e)
    ident = mr.RepeaterIdentity(bytes.fromhex(spec["private_key"]))
    pub = mr.hexs(ident.pub_key)
    if a.expect_pub and a.expect_pub.strip().upper() != pub:
        sys.exit("that private key's public key is %s, not %s: wrong key?" % (pub, a.expect_pub.strip().upper()))

    src = sqlite3.connect(room_db)
    counts = {t: src.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ROOM_TABLES}
    src.close()
    tdir = os.path.join(data, mr.TENANT_DIR)
    tdb = os.path.join(tdir, pub + ".db")
    print("room %r -> tenant room on /%s/" % (spec["name"], spec["path"]))
    print("  public key %s (id %s)" % (pub, pub[:4]))
    print("  " + ", ".join("%d %s" % (n, t.replace("_", " ")) for t, n in counts.items()))
    if os.path.exists(tdb):
        sys.exit("%s already exists: this key is already a tenant room here" % tdb)
    if a.dry_run:
        print("dry run: nothing changed")
        return 0

    # backups
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(data, "backup-before-migration-" + stamp)
    os.makedirs(bdir, mode=0o700)
    shutil.copy2(a.config, os.path.join(bdir, os.path.basename(a.config)))
    with sqlite3.connect(room_db) as s, sqlite3.connect(os.path.join(bdir, "room.db")) as d:
        s.backup(d)                                         # (consistent, WAL included)
    print("backups in %s" % bdir)

    # the tenant's database: the schema, the room's rows, its settings
    os.makedirs(tdir, mode=0o700, exist_ok=True)
    store = mr.Store(tdb)
    store.close()
    db = sqlite3.connect(tdb)
    try:
        db.execute("ATTACH DATABASE ? AS src", (room_db,))
        for t in ROOM_TABLES:
            cols = ", ".join(columns(t))
            db.execute("INSERT INTO %s (%s) SELECT %s FROM src.%s" % (t, cols, cols, t))
        settings = dict(name=spec["name"], path=spec["path"], private_key=spec["private_key"],
                        lat=float(spec["lat"] or 0.0), lon=float(spec["lon"] or 0.0), room_password=spec["room_password"],
                        admin_password=spec["admin_password"], web_password_hash=spec["web_password_hash"])
        settings.update({k: getattr(cfg, k) for k in SETTINGS})
        db.executemany("INSERT OR REPLACE INTO settings (k, v) VALUES (?,?)", [(k, json.dumps(v)) for k, v in settings.items()])
        db.commit()
        db.execute("DETACH DATABASE src")
    except Exception:
        db.close()
        os.remove(tdb)
        raise
    db.close()
    os.chmod(tdb, 0o600)
    got = sqlite3.connect(tdb)
    for t, n in counts.items():
        if got.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] != n:
            got.close()
            os.remove(tdb)
            sys.exit("copy check failed for %s: nothing changed (backups in %s)" % (t, bdir))
    got.close()

    # the config: list the tenant; the main room becomes an empty placeholder
    cfg.set("tenant_rooms", list(cfg.tenant_rooms or []) + [dict(name=spec["name"], path=spec["path"], public_key=pub)])
    if not a.keep_main:
        with sqlite3.connect(room_db) as s:
            for t in ROOM_TABLES:
                s.execute("DELETE FROM %s" % t)
        join = secrets.token_urlsafe(9)[:12]
        cfg.set("name", a.main_name[:31])
        cfg.set("room_password", join)
        cfg.set("allow_read_only", False)
        print("main room emptied, renamed %r, join password set to %s (nobody is expected to join it)" % (a.main_name[:31], join))
    print("done. Start pyMeshRoom: the room is at /%s/. The modem must not still have the old key (a wiped modem "
          "makes a new one)." % spec["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
