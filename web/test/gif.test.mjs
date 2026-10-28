import assert from "node:assert/strict";
import { test } from "node:test";
import { findGifs } from "../src/gif.js";

test("Giphy links, in the forms people paste them", () => {
  const id = "JIX9t2j0ZTN9S";
  for (const url of [
    "https://giphy.com/gifs/cat-funny-" + id,
    "https://giphy.com/gifs/" + id,
    "https://www.giphy.com/gifs/the-office-michael-scott-" + id + "?utm_source=share",
    "https://giphy.com/embed/" + id,
    "https://media.giphy.com/media/" + id + "/giphy.gif",
    "https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjEx/" + id + "/giphy.webp",
    "https://i.giphy.com/" + id + ".gif",
    "https://i.giphy.com/media/" + id + "/giphy.gif",
  ]) {
    const found = findGifs("look: " + url + " lol");
    assert.deepEqual(found.map((g) => [g.id, g.start, g.end]), [[id, 6, 6 + url.length]], url);
  }
});

test("several, and nothing else", () => {
  const text = "a https://giphy.com/gifs/a-abcdef123 and https://i.giphy.com/XYZ987abc.gif";
  assert.deepEqual(findGifs(text).map((g) => g.id), ["abcdef123", "XYZ987abc"]);
  for (const t of ["https://giphy.com/", "https://evilgiphy.com/gifs/abcdef123", "giphy.com/gifs/abcdef123",
    "https://example.com/?u=https:/giphy.com/gifs/x", "https://giphy.com.evil.example/gifs/abcdef123"])
    assert.deepEqual(findGifs(t), [], t);
});
