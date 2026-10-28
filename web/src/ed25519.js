// Ed25519 from MeshCore's 64-byte expanded private key [clamped scalar || prefix]. WebCrypto only takes 32-byte
// seeds, and a key exported from a MeshCore app has no seed, so the curve math is done here with BigInt.
import { concatBytes } from "./bytes.js";
import { sha512 } from "./hash.js";

const P = 2n ** 255n - 19n;
const L = 2n ** 252n + 27742317777372353535851937790883648493n;
const mod = (a, m = P) => ((a % m) + m) % m;

function powMod(b, e, m = P) {
  let r = 1n;
  b = mod(b, m);
  while (e > 0n) {
    if (e & 1n) r = (r * b) % m;
    b = (b * b) % m;
    e >>= 1n;
  }
  return r;
}

const inv = (a) => powMod(a, P - 2n);
const D = mod(-121665n * inv(121666n));

// extended coordinates (X, Y, Z, T)
function add(p, q) {
  const a = mod((p[1] - p[0]) * (q[1] - q[0]));
  const b = mod((p[1] + p[0]) * (q[1] + q[0]));
  const c = mod(2n * p[3] * q[3] * D);
  const d = mod(2n * p[2] * q[2]);
  const e = b - a, f = d - c, g = d + c, h = b + a;
  return [mod(e * f), mod(g * h), mod(f * g), mod(e * h)];
}

function mulPoint(s, p) {
  let q = [0n, 1n, 1n, 0n];
  while (s > 0n) {
    if (s & 1n) q = add(q, p);
    p = add(p, p);
    s >>= 1n;
  }
  return q;
}

const BASE = (() => {
  const y = mod(4n * inv(5n));
  const x2 = mod((y * y - 1n) * inv(D * y * y + 1n));
  let x = powMod(x2, (P + 3n) / 8n);
  if (mod(x * x - x2) !== 0n) x = mod(x * powMod(2n, (P - 1n) / 4n));
  if (x & 1n) x = P - x;
  return [x, y, 1n, mod(x * y)];
})();

const bytesToBig = (b) => b.reduceRight((n, x) => (n << 8n) | BigInt(x), 0n);

function bigToBytes(n, len = 32) {
  const out = new Uint8Array(len);
  for (let i = 0; i < len; i++, n >>= 8n) out[i] = Number(n & 255n);
  return out;
}

function encodePoint(p) {
  const zi = inv(p[2]);
  const x = mod(p[0] * zi), y = mod(p[1] * zi);
  return bigToBytes(y | ((x & 1n) << 255n));
}

// A 32-byte seed becomes the 64-byte key the way MeshCore does it: SHA-512, then clamp.
export function expandSeed(seed) {
  const h = sha512(seed);
  h[0] &= 248;
  h[31] &= 127;
  h[31] |= 64;
  return h;
}

export function ed25519PublicKey(priv64) {
  const a = bytesToBig(priv64.subarray(0, 32)) & ((1n << 255n) - 1n);
  return encodePoint(mulPoint(a, BASE));
}

export function ed25519Sign(priv64, pub, msg) {
  const a = bytesToBig(priv64.subarray(0, 32)) & ((1n << 255n) - 1n);
  const r = mod(bytesToBig(sha512(concatBytes(priv64.subarray(32, 64), msg))), L);
  const R = encodePoint(mulPoint(r, BASE));
  const h = mod(bytesToBig(sha512(concatBytes(R, pub, msg))), L);
  return concatBytes(R, bigToBytes(mod(r + h * a, L)));
}
