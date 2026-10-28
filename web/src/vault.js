// A saved private key, sealed with a passphrase: PBKDF2-HMAC-SHA256 makes the keys, AES-128-CTR hides the private key,
// HMAC-SHA256 checks it. Plain JS, like the rest: WebCrypto exists only on secure origins.
import { aesEncryptBlock, aesExpandKey } from "./aes.js";
import { bytesToHex, concatBytes, equalBytes, hexToBytes, randomBytes, u32le, utf8Encode } from "./bytes.js";
import { hmacSha256 } from "./hash.js";

const ROUNDS = 50000;                     // about a second in a browser: slow for guessing, fine for logging in

export function pbkdf2(password, salt, rounds, length = 32) {
  const out = new Uint8Array(length);
  for (let block = 1, done = 0; done < length; block++) {
    let u = hmacSha256(password, concatBytes(salt, new Uint8Array([block >>> 24, block >>> 16, block >>> 8, block])));
    const t = Uint8Array.from(u);
    for (let i = 1; i < rounds; i++) {
      u = hmacSha256(password, u);
      for (let j = 0; j < t.length; j++) t[j] ^= u[j];
    }
    out.set(t.subarray(0, length - done), done);
    done += t.length;
  }
  return out;
}

function ctr(key16, iv, data) {
  const w = aesExpandKey(key16);
  const out = new Uint8Array(data.length);
  for (let i = 0; i < data.length; i += 16) {
    const ks = aesEncryptBlock(w, concatBytes(iv.subarray(0, 12), u32le(i / 16)));
    for (let j = 0; j < 16 && i + j < data.length; j++) out[i + j] = data[i + j] ^ ks[j];
  }
  return out;
}

function keys(passphrase, salt, rounds) {
  const k = pbkdf2(utf8Encode(passphrase), salt, rounds, 48);
  return { enc: k.subarray(0, 16), mac: k.subarray(16, 48) };
}

// -> {v: 1, rounds, salt, iv, data, mac} (hex), for localStorage
export function seal(secret, passphrase) {
  const salt = randomBytes(16), iv = randomBytes(12);
  const k = keys(passphrase, salt, ROUNDS);
  const data = ctr(k.enc, iv, secret);
  const mac = hmacSha256(k.mac, concatBytes(salt, iv, data));
  return { v: 1, rounds: ROUNDS, salt: bytesToHex(salt), iv: bytesToHex(iv), data: bytesToHex(data), mac: bytesToHex(mac) };
}

// The secret, or null for a wrong passphrase (or a damaged record).
export function unseal(box, passphrase) {
  try {
    const salt = hexToBytes(box.salt), iv = hexToBytes(box.iv), data = hexToBytes(box.data);
    const k = keys(passphrase, salt, Math.min(Math.max(box.rounds | 0, 1000), 1000000));
    if (!equalBytes(hmacSha256(k.mac, concatBytes(salt, iv, data)), hexToBytes(box.mac))) return null;
    return ctr(k.enc, iv, data);
  } catch {
    return null;
  }
}
