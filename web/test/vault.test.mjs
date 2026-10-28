import assert from "node:assert/strict";
import { test } from "node:test";
import { bytesToHex, utf8Encode } from "../src/bytes.js";
import { pbkdf2, seal, unseal } from "../src/vault.js";

test("PBKDF2-HMAC-SHA256 (RFC 7914 test vector)", () => {
  assert.equal(bytesToHex(pbkdf2(utf8Encode("passwd"), utf8Encode("salt"), 1, 64)),
    "55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc49ca9cccf179b645991664b39d77ef317c71b845b1e30bd509112041d3a19783");
});

test("a sealed key opens only with its passphrase", () => {
  const secret = new Uint8Array(64).map((_, i) => i);
  const box = seal(secret, "correct horse");
  assert.equal(box.data.includes(bytesToHex(secret).slice(0, 16)), false);
  assert.deepEqual(unseal(box, "correct horse"), secret);
  assert.equal(unseal(box, "wrong"), null);
  assert.equal(unseal({ ...box, data: "00" + box.data.slice(2) }, "correct horse"), null);
});
