# MeshCore Web

A MeshCore chat client that runs entirely in the browser. pyMeshRoom serves it at `/meshcore-web/` on the
dashboard's port, and it reaches the mesh through that radio over the KaaS API. This folder is its source.

* **Log in with your node's private key, which never leaves the browser.** The page signs a one-time challenge to
  prove you hold the key; no API key needed. Channel messages are encrypted and adverts signed in the page too, so the
  server only ever sees signatures and encrypted packets.
* **Self-contained.** It has no dependencies, no CDN and no backend of its own. The whole app is one file,
  [`meshroom/data/meshcore-web.html`](../meshroom/data/meshcore-web.html). pyMeshRoom serves it, and it also opens
  from disk or from any static web host.
* **Channels.** You join **Public** by default. You can also join **#hashtag** channels (the key comes from the name)
  and **private** channels (a 16-byte secret you share yourself).
* Not yet supported: rooms and direct messages.

## Logging in

| Field | |
|---|---|
| pyMeshRoom URL | The server, e.g. `https://meshroom.example.com`. A path prefix from a reverse proxy works too. Filled in by itself when pyMeshRoom serves the page (at `…/meshcore-web/`). |
| Private key | A 64-byte MeshCore private key in hex, as the apps export it, or a 32-byte seed. **New** makes one. Keep a copy: a new key is a new node. |
| Your name | Goes before your messages (`Name: text`) and into your adverts. |

**Remember** keeps the private key in this browser's `localStorage`. Leave it unticked on a shared computer. Without
it, only the URL and your name are saved.

The server's admin turns web logins on (**Allow web logins** on the dashboard's **Web users** card). Anyone can then
log in and read. You can **send** once the radio has heard your key advertise, for example from your MeshCore app
within range, or once the admin allows your key. Until then the page says so, and it switches over by itself when
that happens. The admin can also disconnect or ban a key.

For a server with web logins turned off, **Use an API key instead** takes a KaaS key (`kaas_…`) from its dashboard.

### How it authenticates

1. The page asks the server for a one-time challenge (`POST /api/v1/web/challenge`).
2. It signs `meshcore-web login v1`, the server's host, the challenge and your public key with your private key, and
   sends the signature back (`POST /api/v1/web/login`). The private key never leaves the browser.
3. It gets a one-time ticket for the websocket (`wss://…/api/v1/kaas?ticket=…`). Each reconnect signs a new challenge.

The label is always the page's own. It never signs anything the server chooses except the challenge, and a login
signature can never be mistaken for a packet signature. Naming the host means another site can't relay a login to
your server. 

## Using it

* **From pyMeshRoom:** open `https://<your server>/meshcore-web/`. The server URL is filled in by itself.
* **Single file:** download [`meshroom/data/meshcore-web.html`](../meshroom/data/meshcore-web.html) and open it, or
  host it anywhere, then type the server's URL. Nothing to install or build.
* **From source:** serve this folder over HTTP(S) and open `index.html`. ES modules don't load from `file://`, so
  use the single file for that.

`meshroom/data/meshcore-web.html` is built from `src/` by `node build.mjs`. A GitHub Action
(`.github/workflows/web.yml`) runs the tests and rebuilds and commits it whenever this folder changes on `main`.
On a pull request, it checks that the committed file matches the source.

If the page is served over HTTPS, the pyMeshRoom URL must be HTTPS as well, because browsers block mixed content.

What you get:

* Messages show the sender, the time, and the hops and SNR as the radio heard them. A flood heard through several
  repeaters shows once.
* `@[name]` mentions are highlighted, and mentions of you stand out.
* **GIFs:** a Giphy link in a message (`https://giphy.com/gifs/…`, as Giphy's Share button copies it) shows as the
  GIF itself. To send one, paste the link; the GIF is previewed while you type. Other MeshCore apps see the link.
  **Show GIFs from Giphy** in the sidebar turns this off: loading a GIF lets Giphy see your IP address.
* Your own messages show **Sent**, **Waiting for the rate limit** or **Not sent: reason**, with **Retry**. Sends are
  queued to fit the key's rate limit (3 packets per 20 s by default).
* Each message holds 160 bytes, `Name: ` included. The counter shows what's left.
* **Advertise me** sends a signed advert, flood or zero-hop, so other people's apps learn your name and key.
* **Details** on a private channel shows its secret to share. Channels and the last 300 messages per channel are kept
  in this browser, per identity.
* It reconnects by itself, logging in again each time. It stops if the admin disconnects or bans the key, turns
  web logins off, or revokes an API key.
* Light and dark themes, and it works on phones.

The radio keeps no backlog: you see messages heard while you're connected.

## Security notes

* All crypto runs in the page, in plain JS: SHA-256/512, HMAC, AES-128 and Ed25519 (`src/hash.js`, `src/aes.js`,
  `src/ed25519.js`). This doesn't depend on WebCrypto, which browsers offer only on secure origins and which can't
  sign with MeshCore's 64-byte expanded keys. The output is checked against pyMeshRoom's Python client
  (`test/vectors.json`).
* The built file has a Content-Security-Policy that allows only its own inline script and style, pinned by hash, so
  no other script can run next to your key. Images may come only from Giphy's two image hosts, and only by GIF id.
  Message text is always added as text, never as HTML.
* An API key is a credential: anyone who has it can use the radio within the key's permissions. Use HTTPS.

## Development

From this folder:

```
node build.mjs                     # ../meshroom/data/meshcore-web.html (CI also does this on main)
npm test                           # unit tests: crypto and packets against the Python client's vectors
test/e2e/run.sh                    # end to end: this pyMeshRoom (fake radio) + Chromium
test/e2e/run.sh built              # the same, against the built single file
python3 tools/make_vectors.py      # regenerate test/vectors.json from ../client
```

The end-to-end run starts pyMeshRoom's own test harness with a dashboard, web logins on and a KaaS key. It covers:

* logging in through the real UI with only a private key: pending until the radio hears the key's advert, then
  sending on Public, a hashtag channel and a private channel, including a burst past the rate limit;
* decrypting what the radio transmitted with the Python client, and checking the advert's signature;
* injecting packets for the page to receive, including Giphy links (Giphy itself is faked);
* persistence, kick and ban by the admin, logging in with an API key, a bad key, the page served by pyMeshRoom at
  `/meshcore-web/`, and the phone layout.

It needs python3 with `cryptography` and `pyserial`, and node with Playwright.

Source layout:

| File | |
|---|---|
| `src/app.js` | The UI |
| `src/kaas.js` | Signed login, ticket, websocket, reconnects, rate-limited send queue |
| `src/gif.js` | Giphy links |
| `src/meshcore.js` | Packets, identities, adverts, channel messages: a port of `client/meshroom_kaas` |
| `src/hash.js`, `src/aes.js`, `src/ed25519.js`, `src/bytes.js` | Crypto and byte helpers |
| `src/store.js` | `localStorage` |
| `build.mjs` | Bundles everything into `../meshroom/data/meshcore-web.html` |
