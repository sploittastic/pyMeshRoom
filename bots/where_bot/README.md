# WhereBot

Answers `!where` on hashtag channels with the repeaters that usually hear a node, from the room's KaaS API
(`near`, see [docs/api.md](../../docs/api.md)).

```
!where @[platinum]          -> platinum is usually near: Seaside 62%, 55b2 25%, the room radio 13%
!where a1b2c3d4e5f60718     -> a1b2c3d4 (aka platinum) is usually near: Seaside 62%, Williams Hill 25%, the room radio 13%
!where @[nobody]            -> I am not familiar with that name. You can try lookup by public key
!where ffeeddccbbaa9988     -> I am not familiar with that public key
```

A key needs at least its first 16 hex characters. Shares are how often each repeater carried the node's floods.
"the room radio" means the room's own radio hears the node directly. A reply has to fit one channel message (160
bytes, "WhereBot: " included), so long repeater names are shortened first, then the least heard repeaters are left out.

## Running it

```
pip install cryptography pyyaml
cp where_bot.example.yaml where_bot.yaml     # then fill it in
python3 where_bot.py where_bot.yaml
```

The API key needs Receive and Send. The bot reconnects on its own if the connection drops.
