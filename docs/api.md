# KaaS: the radio as an API

KaaS ("KISS radio as an API service") lets bots and other programs use pyMeshRoom's radio over a websocket. A client
with an API key can receive every packet the radio hears and sends, and can send raw MeshCore packets of its own.

The server never decrypts anything for a client. Clients do all MeshCore crypto themselves: they are nodes on the
mesh with their own identities. The [Python client](../client/README.md) does that work for you: adverts, contacts,
DMs, channels and joining rooms. The machine-readable description is [asyncapi.yaml](asyncapi.yaml).

## API keys

The main admin makes keys on the dashboard's **KaaS** card:

* **Receive**: stream every packet.
* **Send**: send packets.
* A description, and an optional expiry date. The key stops working at the end of that day, in the browser's time
  zone.

**Save** shows the key once. pyMeshRoom keeps only a SHA-256 hash of it, in `kaas_keys` in the config. A lost key
can't be recovered: revoke it and make a new one.

The list on the card shows each key's description, access, expiry, last use and current connections. The key itself
is never shown again. **Revoke** deletes the key and disconnects its clients at once. When a key expires, its clients are
disconnected within a second.

Admin endpoints use the dashboard's admin login cookie:

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/v1/kaas/keys` | | `{"keys": [...], "path", "send_burst", "send_window_s", "airtime_pct", "stats"}` |
| POST | `/api/v1/kaas/keys` | `{"receive": bool, "send": bool, "description": str, "expires": epoch or null}` | `{"ok": true, "id", "token"}` |
| POST | `/api/v1/kaas/keys/<id>/revoke` | | `{"ok": true}` |

## Connecting

```
GET /api/v1/kaas HTTP/1.1
Upgrade: websocket
Authorization: Bearer kaas_...
```

Connect to `ws://<server>:<web_port>/api/v1/kaas`, on the dashboard's port. The dashboard serves plain HTTP. If
clients connect over the internet, put pyMeshRoom behind an HTTPS reverse proxy that passes websocket upgrades and
use `wss://`. Otherwise the key travels unencrypted.

Failed handshakes return:

* 401: missing, unknown, revoked or expired key, after a one-second delay.
* 503: too many clients. The limit is 16 connections across all keys.

Every message in both directions is a JSON text frame. The server pings every 30 s. A connection that sends nothing
back for 90 s, not even a pong, is closed.

Close codes from the server:

* 4001: the key was revoked.
* 4002: the key expired.
* 1001: the server is shutting down.
* 1002: a protocol error (the reason says which).

## Server to client

**hello**, the first message:

```json
{"type": "hello", "api": 1, "server": "meshroom-py 4.9",
 "key": {"id": "3f2a9c1b", "receive": true, "send": true, "expires": null},
 "send_burst": 3, "send_window_s": 20, "airtime_pct": 5,
 "radio": {"freq_hz": 910525000, "bw_hz": 62500, "sf": 7, "cr": 5},
 "nodes": {"room": "<64 hex>", "tenants": [], "repeater": null, "companion": null}}
```

`nodes` lists the public keys of the nodes running on this radio.

**packet**: one for every packet the radio hears (`"dir": "rx"`) or sends (`"dir": "tx"`). Keys that may receive only.

```json
{"type": "packet", "dir": "rx", "ts": 1760313600.123, "raw": "1500a1b2...",
 "ptype": "txt", "route": "flood", "hops": 2, "hash": "9f3c...", "snr": 7.25, "rssi": -96}
```

* `raw` is the whole packet in hex, exactly as on the air.
* `ptype`, `route`, `hops` and `hash` (the 8-byte id MeshCore de-duplicates on) save parsing. They are missing if the
  packet doesn't parse.
* `snr` and `rssi` come with rx packets only.

Floods are heard many times, once per repeater, so de-duplicate on `hash`.

The tx packets matter: the radio never hears its own transmissions. Replies from a room on this radio to your bot
arrive as tx packets.

**send_result**: the fate of each packet you send, matched by your `id`.

* `queued`: accepted, waiting for the air.
* `sent`: transmitted.
* `local`: the packet was only for a node on this radio (a room here, for example). That node got it directly, and
  it never went on the air.
* `dropped`: refused, with a `reason`. When waiting would help, `retry_after` says how many seconds.

```json
{"type": "send_result", "id": 7, "status": "dropped",
 "reason": "rate limit: 3 packets per 20 s per key (ACKs and paths excepted)", "retry_after": 6.2}
```

Other messages:

* `{"type": "dropped", "count": n}`: you read too slowly. Up to 512 messages wait for each client. Past that, the
  oldest are discarded and this tells you how many.
* `{"type": "pong", "id": ...}`: the answer to your ping.
* `{"type": "error", "error": "..."}`: a message the server couldn't use.

## Client to server

**send**: needs a key that may send. `id` is optional, any string or number up to 64 characters, and is echoed in the results.

```json
{"type": "send", "id": 7, "raw": "1100a1b2..."}
```

**ping**: `{"type": "ping", "id": 1}`

## Sending rules

* **3 packets in any 20 s per key** (`kaas_send_burst`, `kaas_send_window_s`), a rolling window, so a bot can send a
  short burst. ACKs and path returns are exempt: a bot has to answer
  what it receives, or room joins and DMs fail. Path returns usually carry an ACK.
* **An airtime cap shared by every key**: 5% of airtime (`kaas_airtime_pct`) over a rolling 10 minutes. This one
  includes ACKs.
* **Lowest priority**: the room's ACKs and replies go first, then the virtual repeater's relays, then room pushes,
  then API packets. Packets other than ACKs and path returns also wait for a quiet moment on the channel, as room
  pushes do.
* **Checks**: the packet must parse as MeshCore, with a payload of at most 184 bytes. Adverts and logins that claim the
  identity of a node on this radio are refused.
* **Shared with this radio**: a packet you send is offered to the room, tenant rooms, virtual companion and virtual
  repeater here first, as if they heard it. A flood or zero-hop packet that one of them takes stays off the air
  (`local`). Everything else is transmitted and is never relayed back by the room's own repeater.

## Settings

| Setting | Default | |
|---|---|---|
| `kaas_keys` | `[]` | The keys, hashed. Managed from the dashboard. |
| `kaas_send_burst` | 3 | Per key: at most this many packets... |
| `kaas_send_window_s` | 20 | ...in any this many seconds. ACKs and path returns are excepted. |
| `kaas_airtime_pct` | 5 | All API clients together: at most this share of airtime. |

## Writing your own client

Anything that speaks websockets and JSON works. In Python, use the [client](../client/README.md). Otherwise:

1. Connect with the `Authorization` header and read `hello`.
2. For each `packet`, decode `raw` and de-duplicate on `hash`.
3. Send `{"type": "send", "raw": ...}` and match `send_result` by `id`.

The MeshCore packet format and crypto are described in the MeshCore firmware source. `client/meshroom_kaas/node.py`
is a compact, working reference for adverts, DMs (with their ACKs and path returns), channels and room logins.
