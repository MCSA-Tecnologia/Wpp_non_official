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
  contact_name: string;
}

export interface MessageCard {
  text: string;
  url: string;
  image: Uint8Array;
}

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
  send(phone: string, message: string, card: MessageCard): Promise<string>;
  close(): Promise<void>;
}
