import type { ClaimedJob, OutboundMessage, WhatsAppConnector } from "./types.js";

export function composeNativeLinkText(message: string, url: string, showUrl: boolean): string {
  return showUrl ? `${url.trim()}\n\n${message.trim()}` : message.trim();
}

export async function prepareOutboundMessage(
  job: ClaimedJob,
  loadImage: (assetId: string) => Promise<Uint8Array>,
): Promise<{ message: OutboundMessage; imageError?: unknown }> {
  const text = composeNativeLinkText(job.message, job.card_url, job.card_show_url);
  try {
    if (!job.card_asset_id) throw new Error("Imagem personalizada não configurada");
    const thumbnail = await loadImage(job.card_asset_id);
    if (thumbnail.byteLength === 0) throw new Error("Imagem personalizada vazia");
    return job.card_show_url
      ? {
          message: {
            format: "custom_native_link",
            text,
            url: job.card_url,
            title: job.card_text,
            thumbnail,
          },
        }
      : {
          message: {
            format: "interactive_link",
            text,
            url: job.card_url,
            title: job.card_text,
            thumbnail,
          },
        };
  } catch (imageError) {
    return {
      message: job.card_show_url
        ? { format: "native_link_fallback", text }
        : {
            format: "interactive_link",
            text,
            url: job.card_url,
            title: job.card_text,
          },
      imageError,
    };
  }
}

export function sendPreparedMessage(
  connector: WhatsAppConnector,
  phone: string,
  message: OutboundMessage,
): Promise<string> {
  return connector.send(phone, message);
}
