import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInteractiveMessageContent,
  buildMessageContent,
} from "../dist/connectors/baileys.js";
import {
  composeNativeLinkText,
  prepareOutboundMessage,
  sendPreparedMessage,
} from "../dist/outbound-message.js";

const job = {
  id: "job-1",
  lease_token: "lease-1",
  phone: "+5531999999999",
  message: "Olá Maria, proposta do Banco A.",
  card_text: "Consulte sua negociação",
  card_url: "https://example.com/negociacao",
  card_asset_id: "asset-1",
  card_show_url: true,
  contact_name: "Maria",
};

test("the native link preview fallback dependency is installed", async () => {
  const module = await import("link-preview-js");
  assert.equal(typeof module.getLinkPreview, "function");
});

test("native link text puts the visible URL before the personalized text", () => {
  assert.equal(
    composeNativeLinkText(
      " Olá Maria, proposta do Banco A. ",
      " https://example.com/negociacao ",
      true,
    ),
    "https://example.com/negociacao\n\nOlá Maria, proposta do Banco A.",
  );
});

test("an unchecked setting builds an interactive card with an Acessar button and no visible URL", async () => {
  const thumbnail = new Uint8Array([255, 216, 255, 217]);
  const prepared = await prepareOutboundMessage(
    { ...job, card_show_url: false },
    async () => thumbnail,
  );

  assert.equal(prepared.message.format, "interactive_link");
  assert.equal(prepared.message.text, "Olá Maria, proposta do Banco A.");
  assert.equal(prepared.message.url, job.card_url);
  assert.equal(prepared.message.title, job.card_text);
  assert.deepEqual(prepared.message.thumbnail, thumbnail);

  const imageMessage = { url: "https://upload.example/image", directPath: "/image" };
  const content = buildInteractiveMessageContent(prepared.message, imageMessage);
  const interactive = content.viewOnceMessage.message.interactiveMessage;
  assert.equal(interactive.body.text.includes("https://"), false);
  assert.equal(interactive.body.text, job.message);
  assert.equal(interactive.header.title, job.card_text);
  assert.equal(interactive.header.hasMediaAttachment, true);
  assert.equal(interactive.header.imageMessage.url, imageMessage.url);
  assert.equal(interactive.header.imageMessage.directPath, imageMessage.directPath);
  assert.equal(interactive.nativeFlowMessage.buttons.length, 1);
  assert.equal(interactive.nativeFlowMessage.buttons[0].name, "cta_url");
  assert.deepEqual(
    JSON.parse(interactive.nativeFlowMessage.buttons[0].buttonParamsJson),
    {
      display_text: "Acessar",
      url: job.card_url,
      merchant_url: job.card_url,
    },
  );
});

test("custom title and image use the native WhatsApp link preview payload", async () => {
  const thumbnail = new Uint8Array([255, 216, 255, 217]);
  const prepared = await prepareOutboundMessage(job, async () => thumbnail);

  assert.equal(prepared.imageError, undefined);
  assert.deepEqual(prepared.message, {
    format: "custom_native_link",
    text: "https://example.com/negociacao\n\nOlá Maria, proposta do Banco A.",
    url: "https://example.com/negociacao",
    title: "Consulte sua negociação",
    thumbnail,
  });
  assert.deepEqual(buildMessageContent(prepared.message), {
    text: prepared.message.text,
    linkPreview: {
      "canonical-url": "https://example.com/negociacao",
      "matched-text": "https://example.com/negociacao",
      title: "Consulte sua negociação",
      jpegThumbnail: Buffer.from(thumbnail),
    },
  });
});

test("an image load failure falls back to the site's native metadata before sending", async () => {
  const prepared = await prepareOutboundMessage(job, async () => {
    throw new Error("asset unavailable");
  });

  assert.equal(prepared.message.format, "native_link_fallback");
  assert.match(String(prepared.imageError), /asset unavailable/);
  assert.deepEqual(buildMessageContent(prepared.message), { text: prepared.message.text });
});

test("an unchecked setting keeps one interactive card when the image cannot be loaded", async () => {
  const prepared = await prepareOutboundMessage(
    { ...job, card_show_url: false },
    async () => {
      throw new Error("asset unavailable");
    },
  );

  assert.equal(prepared.message.format, "interactive_link");
  assert.equal(prepared.message.thumbnail, undefined);
  assert.match(String(prepared.imageError), /asset unavailable/);
  const content = buildInteractiveMessageContent(prepared.message);
  const interactive = content.viewOnceMessage.message.interactiveMessage;
  assert.equal(interactive.header.hasMediaAttachment, false);
  assert.equal(interactive.nativeFlowMessage.buttons[0].name, "cta_url");
});

test("a provider error results in one attempt without an alternate send", async () => {
  const calls = [];
  const connector = {
    send: async (phone, message) => {
      calls.push({ phone, message });
      throw new Error("ambiguous provider error");
    },
  };
  const prepared = await prepareOutboundMessage(job, async () => new Uint8Array([1]));

  await assert.rejects(
    sendPreparedMessage(connector, job.phone, prepared.message),
    /ambiguous provider error/,
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].message.format, "custom_native_link");
});
