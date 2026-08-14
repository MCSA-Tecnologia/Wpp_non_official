export type AccountState =
  | "offline"
  | "connecting"
  | "qr_required"
  | "ready"
  | "degraded"
  | "backoff"
  | "logged_out"
  | "disabled";

export interface ClaimedAccount {
  id: string;
  external_id: string;
  display_name: string;
  state: AccountState;
  lease_until: string;
  session_revision: number;
}

export interface ClaimedJob {
  id: string;
  lease_token: string;
  phone: string;
  message: string;
  card_text: string;
  card_url: string;
  card_asset_id: string;
  card_show_url: boolean;
  contact_name: string;
}

export type OutboundMessage =
  | {
      format: "custom_native_link";
      text: string;
      url: string;
      title: string;
      thumbnail: Uint8Array;
    }
  | {
      format: "native_link_fallback";
      text: string;
    }
  | {
      format: "interactive_link";
      text: string;
      url: string;
      title: string;
      thumbnail?: Uint8Array;
    };

export interface AuthRecord {
  category: string;
  key_id: string;
  value: unknown;
}

export interface ConnectorEvents {
  onState: (state: AccountState, error?: string, phone?: string) => Promise<void>;
  onQr: (qr: string) => Promise<void>;
  onAck: (providerMessageId: string, ackLevel: number, payload?: object) => Promise<void>;
}

export interface WhatsAppConnector {
  connect(): Promise<void>;
  isReady(): boolean;
  validateRecipient(phone: string): Promise<boolean>;
  send(phone: string, message: OutboundMessage): Promise<string>;
  close(): Promise<void>;
}
