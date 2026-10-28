// MeshCore packets, identities, adverts and channel messages: a port of pyMeshRoom's client/meshroom_kaas.
import { aesEcbDecrypt, aesEcbEncrypt } from "./aes.js";
import {
  base64ToBytes, bytesToHex, concatBytes, equalBytes, hexToBytes, i32le, readU32le, u32le, utf8Cut, utf8Decode,
  utf8Encode,
} from "./bytes.js";
import { ed25519PublicKey, ed25519Sign, expandSeed } from "./ed25519.js";
import { hmacSha256, sha256 } from "./hash.js";

export const ROUTE_TRANSPORT_FLOOD = 0;
export const ROUTE_FLOOD = 1;
export const ROUTE_DIRECT = 2;
export const ROUTE_TRANSPORT_DIRECT = 3;
export const PT_ADVERT = 4;
export const PT_GRP_TXT = 5;
export const PT_TRACE = 9;
export const ADV_TYPE_CHAT = 1;
const ADV_LATLON_MASK = 0x10, ADV_NAME_MASK = 0x80;
const MAX_ADVERT_DATA_SIZE = 32;
export const MAX_TEXT_LEN = 160;            // a channel message, "Name: " included
export const MAX_NAME_LEN = 31;             // what fits an advert with no location
const MAX_PACKET_PAYLOAD = 184;
const MAX_PATH_SIZE = 64;
const CIPHER_MAC_SIZE = 2;
export const PUBLIC_CHANNEL_KEY = base64ToBytes("izOH6cXN6mrJ5e26oRXNcg==");   // the well-known "Public" channel

// ---------------------------------------------------------------- packets

const pathBytes = (pathLen) => (pathLen & 63) * ((pathLen >> 6) + 1);
const pathValid = (pathLen) => (pathLen >> 6) + 1 !== 4 && pathBytes(pathLen) <= MAX_PATH_SIZE;

// header (route type: low 2 bits, payload type: next 4), optional transport codes, path_len, path, payload
export function parsePacket(raw) {
  if (raw.length < 2) return null;
  let i = 0;
  const header = raw[i++];
  const routeType = header & 3;
  const hasTransport = routeType === ROUTE_TRANSPORT_FLOOD || routeType === ROUTE_TRANSPORT_DIRECT;
  if (hasTransport) {
    if (raw.length < i + 4) return null;
    i += 4;
  }
  if (raw.length <= i) return null;
  const pathLen = raw[i++];
  if (!pathValid(pathLen)) return null;
  const path = raw.subarray(i, i + pathBytes(pathLen));
  i += path.length;
  if (i >= raw.length) return null;
  const payload = raw.subarray(i);
  if (payload.length > MAX_PACKET_PAYLOAD) return null;
  return {
    header, routeType, ptype: (header >> 2) & 15, pathLen, path, payload,
    hops: pathLen & 63, isFlood: routeType === ROUTE_FLOOD || routeType === ROUTE_TRANSPORT_FLOOD,
  };
}

export function floodPacket(ptype, payload) {
  return concatBytes(new Uint8Array([((ptype & 15) << 2) | ROUTE_FLOOD, 0]), payload);
}

export function zeroHopPacket(ptype, payload) {
  return concatBytes(new Uint8Array([((ptype & 15) << 2) | ROUTE_DIRECT, 0]), payload);
}

// What MeshCore nodes de-duplicate on: the same packet heard along different paths has the same hash.
export function packetHash(pkt) {
  const parts = [new Uint8Array([pkt.ptype])];
  if (pkt.ptype === PT_TRACE) parts.push(new Uint8Array([pkt.pathLen & 255, pkt.pathLen >> 8]));
  parts.push(pkt.payload);
  return bytesToHex(sha256(concatBytes(...parts)).subarray(0, 8));
}

// ---------------------------------------------------------------- identity

// A node identity from a 64-byte MeshCore private key (as the apps export it) or a 32-byte seed, in hex.
export class Identity {
  constructor(privateHex) {
    let key = hexToBytes(privateHex);
    if (key.length === 32) key = expandSeed(key);
    if (key.length !== 64) throw new Error("a private key is 64 bytes (128 hex characters), or a 32-byte seed");
    this.privateKey = key;
    this.pub = ed25519PublicKey(key);
    this.pubHex = bytesToHex(this.pub);
  }

  static generate() {
    return new Identity(bytesToHex(crypto.getRandomValues(new Uint8Array(32))));
  }

  get privateHex() {
    return bytesToHex(this.privateKey);
  }

  sign(msg) {
    return ed25519Sign(this.privateKey, this.pub, msg);
  }
}

// An advert payload: lets other nodes learn this node's name and key.
export function advertPayload(identity, name, ts, lat = 0, lon = 0) {
  let flags = ADV_TYPE_CHAT;
  let body = new Uint8Array(0);
  if (lat || lon) {
    flags |= ADV_LATLON_MASK;
    body = concatBytes(i32le(Math.round(lat * 1e6)), i32le(Math.round(lon * 1e6)));
  }
  const nameBytes = utf8Cut(utf8Encode(name), MAX_ADVERT_DATA_SIZE - 1 - body.length);
  if (nameBytes.length) flags |= ADV_NAME_MASK;
  const app = concatBytes(new Uint8Array([flags]), body, nameBytes);
  const tsb = u32le(ts);
  return concatBytes(identity.pub, tsb, identity.sign(concatBytes(identity.pub, tsb, app)), app);
}

// ---------------------------------------------------------------- channels

export function hashtagChannelKey(name) {
  return sha256(utf8Encode(name.startsWith("#") ? name : "#" + name)).subarray(0, 16);
}

export const channelHash = (key16) => sha256(key16)[0];

// MAC (2 bytes of HMAC-SHA256 keyed with the 32-byte secret) + AES-128-ECB of the zero-padded text.
export function encryptThenMac(secret32, plain) {
  const ct = aesEcbEncrypt(secret32.subarray(0, 16), plain);
  return concatBytes(hmacSha256(secret32, ct).subarray(0, CIPHER_MAC_SIZE), ct);
}

// The plaintext (still zero-padded), or null when the MAC doesn't match.
export function macThenDecrypt(secret32, data) {
  if (data.length <= CIPHER_MAC_SIZE || (data.length - CIPHER_MAC_SIZE) % 16) return null;
  const ct = data.subarray(CIPHER_MAC_SIZE);
  if (!equalBytes(hmacSha256(secret32, ct).subarray(0, CIPHER_MAC_SIZE), data.subarray(0, CIPHER_MAC_SIZE))) return null;
  return aesEcbDecrypt(secret32.subarray(0, 16), ct);
}

// {name, kind: "public" | "hashtag" | "private", key (16 bytes), keyHex, hash}
export function makeChannel(name, kind, keyHex) {
  let key;
  if (kind === "public") key = PUBLIC_CHANNEL_KEY;
  else if (kind === "hashtag") key = hashtagChannelKey(name);
  else {
    key = hexToBytes(keyHex);
    if (key.length !== 16) throw new Error("a channel secret is 16 bytes (32 hex characters)");
  }
  return { name, kind, key, keyHex: bytesToHex(key), hash: channelHash(key), secret: concatBytes(key, new Uint8Array(16)) };
}

// The bytes of text left after "Name: ".
export function textBudget(senderName) {
  return Math.max(0, MAX_TEXT_LEN - utf8Encode(senderName + ": ").length);
}

// A channel message, as the apps send it: "Name: text" with a timestamp, flooded. Returns the raw packet.
export function channelMessagePacket(channel, senderName, text, ts) {
  const prefix = utf8Encode(senderName + ": ");
  const body = utf8Cut(utf8Encode(text), textBudget(senderName));
  const plain = concatBytes(u32le(ts), new Uint8Array([0]), prefix, body);
  return floodPacket(PT_GRP_TXT, concatBytes(new Uint8Array([channel.hash]), encryptThenMac(channel.secret, plain)));
}

// A GRP_TXT payload -> {channel, sender, text, ts}, or null if it's for none of these channels.
export function readChannelMessage(channels, payload) {
  if (payload.length <= 3) return null;
  for (const ch of channels) {
    if (ch.hash !== payload[0]) continue;                       // (several channels may share a hash byte)
    const data = macThenDecrypt(ch.secret, payload.subarray(1));
    if (!data) continue;
    if (data.length < 5 || data[4] >> 2) return null;           // not plain text
    let end = data.indexOf(0, 5);
    if (end < 0) end = data.length;
    const full = utf8Decode(data.subarray(5, end));
    const sep = full.indexOf(": ");
    return {
      channel: ch, ts: readU32le(data, 0),
      sender: sep < 0 ? "" : full.slice(0, sep), text: sep < 0 ? full : full.slice(sep + 2),
    };
  }
  return null;
}
