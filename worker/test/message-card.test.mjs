import assert from "node:assert/strict";
import test from "node:test";

import { buildCardMessage } from "../dist/connectors/baileys.js";


test("buildCardMessage attaches one clickable card without duplicating the URL in text", () => {
  const image = new Uint8Array([255, 216, 255, 217]);
  const result = buildCardMessage("Olá Maria", {
    text: "Consulte sua negociação",
    url: "https://example.com/negociacao",
    image,
  });

  assert.equal(result.text, "Olá Maria");
  assert.equal(result.text.includes("https://"), false);
  assert.deepEqual(result.contextInfo.externalAdReply, {
    title: "Consulte sua negociação",
    body: "example.com",
    mediaType: 1,
    thumbnail: image,
    sourceUrl: "https://example.com/negociacao",
    renderLargerThumbnail: true,
    showAdAttribution: false,
  });
});
