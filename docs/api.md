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
is never shown again. It also shows each key's counts, for the last 24 hours and in total:

* **Received**: packets streamed to it.
* **Sent**: packets it sent that were accepted.
* **Errors**: refused sends and bad requests.

The counts are kept in `kaas_stats.json` in `data_dir`, saved every 5 minutes and at shutdown. Revoking a key deletes
its counts. **Revoke** deletes the key and disconnects its clients at once. When a key expires, its clients are
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

### From a browser

A browser can't put an `Authorization` header on a websocket, so it trades the key for a one-time ticket first:

```
POST /api/v1/kaas/ticket HTTP/1.1
Authorization: Bearer kaas_...
```

```json
{"ticket": "kt_...", "expires_in": 30}
```

Then it connects to `wss://<server>/api/v1/kaas?ticket=kt_...` with no header. A ticket opens one connection, within
30 seconds, as the key it was made for; ask for a new one to reconnect. A missing, unknown, revoked or expired key gets
401 after a one-second delay, and 503 means too many tickets are waiting (at most 64). The ticket endpoint answers
CORS preflights for any origin: no cookies are involved, so the key itself is the only credential. Use it over HTTPS.

```js
const r = await fetch("https://meshroom.example/api/v1/kaas/ticket", {method: "POST", headers: {Authorization: "Bearer " + key}});
const {ticket} = await r.json();
const ws = new WebSocket("wss://meshroom.example/api/v1/kaas?ticket=" + encodeURIComponent(ticket));
```

### Logging in with a node's private key (MeshCore Web)

People can log in without an API key, by signing with their MeshCore node's private key. The key never leaves their
browser; only a signature does. The admin turns this on with **Allow web logins** on the dashboard's **Web users**
card (`web_login`, off by default).

1. `POST /api/v1/web/challenge` returns `{"challenge": "wc_...", "expires_in": 60, "label": "meshcore-web login v1"}`.
2. Sign these lines, joined with `\n`, using Ed25519 and the node's key:
   `meshcore-web login v1`, the server's host as the browser reached it (e.g. `mesh.example.com` or
   `10.0.0.5:8081`), the challenge, and the public key in lowercase hex.
3. `POST /api/v1/web/login` with
   `{"pub": "<64 hex>", "signature": "<128 hex>", "challenge": "wc_...", "host": "...", "name": "pl@web"}`.
   The answer is `{"ticket": "kt_...", "expires_in": 30, "send": true, "state": "allowed"}`, or
   `"send": false, "state": "pending"`.
4. Connect with `?ticket=`, as above.

A challenge works once. The host must be the one this request arrived at: its `Host` header, `X-Forwarded-Host` from
a reverse proxy on the same machine, or one listed in `web_hosts`. This stops another site from getting a visitor to
sign a login for this server. The label means a login signature can never also be a valid MeshCore packet
signature.

Anyone may log in and receive. A key may **send** once this radio has heard it advertise, or once the admin presses
**Allow send** for it. An advert sent through this API doesn't count: it has to be heard on the air. With
`web_trust_observers`, an advert reported by an MQTT observer counts too. Until then the session is **pending**:
sends are refused, and `{"type": "access", "send": true, "state": "allowed"}` arrives when that changes (it also
arrives with `false` if the admin takes it back).

A web session may only send what a chat client needs, and only as itself:

* adverts for its own key, and room logins (`anon_req`) from its own key;
* DMs, requests, responses and path returns whose sender hash is its own key's first byte;
* channel messages and ACKs, which carry no sender identity in MeshCore;
* nothing else: no traces, control or raw packets.

It shares the API's rate limit (per key, so per web user) and the airtime cap. `hello` says it's a web login:
`"key": {"id": "w:<pub>", "receive": true, "send": false, "expires": null, "web": true, "pub": "<64 hex>",
"state": "pending"}`.

On the **Web users** card, the admin sees each key's name, public key, access, last activity, connections and packet
counts, and can:

* **Allow send**: let a key send without being heard;
* **Kick**: disconnect it, though it can log in again;
* **Ban**: disconnect it and refuse it from then on; a key can also be banned before it ever logs in.

A banned person can make a new key, but a new key can't send until it's heard on the air or allowed.

Failed handshakes return:

* 401: missing, unknown, revoked or expired key, after a one-second delay.
* 503: too many clients. The limit is 16 connections across all API keys, and `web_max_clients` (32) web sessions on
  top of that.

Every message in both directions is a JSON text frame. The server pings every 30 s. A connection that sends nothing
back for 90 s, not even a pong, is closed.

Close codes from the server:

* 4001: the key was revoked.
* 4002: the key expired.
* 4003: a web login's key was banned.
* 4004: web logins were turned off.
* 4005: the admin disconnected this web session (it may log in again).
* 1001: the server is shutting down.
* 1002: a protocol error (the reason says which).

## Server to client

**hello**, the first message:

```json
{"type": "hello", "api": 1, "server": "meshroom-py 4.9",
 "key": {"id": "3f2a9c1b", "receive": true, "send": true, "expires": null},
 "send_burst": 3, "send_window_s": 20, "airtime_pct": 5,
 "radio": {"freq_hz": 910525000, "bw_hz": 62500, "sf": 7, "cr": 5},
 "nodes": {"room": "<64 hex>", "tenants": [], "repeater": null, "companion": null},
 "names": {"<64 hex>": "My Room"}}
```

`nodes` lists the public keys of the nodes running on this radio, and `names` gives the room's and each tenant room's
name by public key.

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
* `{"type": "access", "send": bool, "state": "allowed" | "pending"}`: a web login's permission to send changed.

## Client to server

**send**: needs a key that may send. `id` is optional, any string or number up to 64 characters, and is echoed in the results.

```json
{"type": "send", "id": 7, "raw": "1100a1b2..."}
```

**ping**: `{"type": "ping", "id": 1}`

### Route questions

Any key may ask these. They come from what the room has learned of the mesh. The answers are routes from this
radio, which is where your packets go out.

**near**: which repeaters hear a node, meaning its first hop into the mesh, and each one's share. It is learned from
the floods the radio hears from that node, faded over hours. At most 4 repeaters are kept per node. `node` is its
public key in hex; the first 8 bytes are enough. Instead of `node` you can send `name`: the name the node
advertises, plain or as a mention like `@[platinum]`, ignoring case. If several nodes advertise that name, the one
with known repeaters wins, then the newest advert. An unheard name is an error. `limit` is 1 to 10, default 4.

```json
{"type": "near", "id": "q1", "node": "a1b2c3d4e5f60718"}
{"type": "near", "id": "q2", "name": "@[platinum]"}
{"type": "near_result", "id": "q1", "node": "a1b2c3d4e5f60718", "name": "platinum",
 "repeaters": [{"id": "44c3", "name": "Seaside", "share": 0.62}, {"id": "55b2", "name": null, "share": 0.25},
               {"direct": true, "id": "9a1f", "name": "pyMeshRoom Virtual Repeater", "share": 0.13}]}
```

`"direct": true` means this radio hears the node itself. Its `id` and `name` are then this radio's virtual repeater,
which is what relays for that node, or the room when the virtual repeater is off. An unknown node gets an empty list.

**routes**: the best known routes from this radio to a repeater, best first. They are learned from flood paths, and
scored by how often each was seen and whether the room's pushes over it were delivered. `repeater` is its public key
or id in hex; the first 2 bytes are enough. `limit` is 1 to 10, default 3.

```json
{"type": "routes", "id": "q2", "repeater": "44c3"}
{"type": "routes_result", "id": "q2", "repeater": "44c3", "name": "Seaside",
 "routes": [{"path_len": 66, "path": "55b244c3", "hops": ["55b2", "44c3"], "names": [null, "Seaside"],
             "confidence": 0.81}]}
```

The repeater is the last hop. `path_len` and `path` go straight into a direct packet's header and path. Together the
two answers give a route to a node you have no path for: send direct along the best route to the repeater that hears
it most. A first hop that is this radio's own virtual repeater is dropped when the packet is sent, as the room does.

**route**: suggested routes between any two nodes, not just from this radio. `from` and `to` are each a node's public
key in hex (at least 8 bytes), its name or `@[name]`, or a repeater's 2-byte id. The room takes the repeaters near
each end (as in `near`) and finds the best chains between them through the repeater links it has seen: links in the
flood paths this radio heard, plus links the MQTT observers reported, faded over a day. `limit` is 1 to 5, default 3.

```json
{"type": "route", "id": "q3", "from": "a1b2c3d4e5f60718", "to": "@[platinum]"}
{"type": "route_result", "id": "q3", "from": {"node": "a1b2c3d4e5f60718", "name": "Jeff"},
 "to": {"node": "0f1e2d3c4b5a6978", "name": "platinum"},
 "routes": [{"path_len": 67, "path": "a1a1b2b2d4d4", "hops": ["a1a1", "b2b2", "d4d4"],
             "names": ["Hilltop", "Seaside", null], "score": 0.62},
            {"path_len": 67, "path": "a1a1c3c3d4d4", "hops": ["a1a1", "c3c3", "d4d4"],
             "names": ["Hilltop", "Ridge", null], "score": 0.19}]}
```

The path runs from the `from` end: its first hop is a repeater that hears the source, its last one a repeater the
destination hears. `score` (0 to 1) is how likely the ends use those repeaters times each link's confidence. A busy
link is seen hundreds of times, so a link counts by how it compares with its repeater's strongest link: the
strongest scores 1, one with a quarter of that traffic 0.5. A link seen only once counts half again. Weak links
(below 0 dB SNR, where known) count for less, down to half at -15 dB. A link seen only in the other direction counts
at half, and every hop costs 5%, so shorter routes win ties. Routes use 2-byte ids, at most 10 hops. This radio is a hop only while its
virtual repeater relays. A node with no recent location (it hasn't flood-adverted lately) is an error, and so is an
unknown name. An empty list means no chain of seen links joins the two ends.
Bad input gets `{"type": "error", "id": ..., "error": ...}`. Questions share the 4-at-a-time limit with sends.

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
| `web_login` | false | MeshCore Web: people may log in by signing with their node's key. |
| `web_max_clients` | 32 | Web sessions at once, apart from API keys' 16. |
| `web_approved` | `[]` | Public keys (hex) allowed to send without being heard. Managed from the dashboard. |
| `web_banned` | `[]` | `{"pub", "name", "ts"}` of banned keys. Managed from the dashboard. |
| `web_trust_observers` | false | A key heard only by an MQTT observer may send too. |
| `web_hosts` | `[]` | More host names a login may be signed for, behind a proxy elsewhere that rewrites `Host`. |

## Writing your own client

Anything that speaks websockets and JSON works. In Python, use the [client](../client/README.md). Otherwise:

1. Connect with the `Authorization` header (or, from a browser, a ticket) and read `hello`.
2. For each `packet`, decode `raw` and de-duplicate on `hash`.
3. Send `{"type": "send", "raw": ...}` and match `send_result` by `id`.

The MeshCore packet format and crypto are described in the MeshCore firmware source. `client/meshroom_kaas/node.py`
is a compact, working reference for adverts, DMs (with their ACKs and path returns), channels and room logins.
