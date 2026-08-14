export type Role = "admin" | "operator";
export type AccountState =
  | "offline"
  | "connecting"
  | "qr_required"
  | "ready"
  | "degraded"
  | "backoff"
  | "logged_out"
  | "disabled";

export interface User {
  id: string;
  email: string;
  role: Role;
}

export interface Account {
  id: string;
  external_id: string;
  display_name: string;
  phone: string | null;
  state: AccountState;
  enabled: boolean;
  node_id: string | null;
  last_heartbeat_at: string | null;
  last_error: string | null;
  qr_code: string | null;
  sent_today: number;
  reconnect_count: number;
}

export interface ImportRow {
  id: string;
  row_number: number;
  raw_data: Record<string, string>;
  normalized_phone: string | null;
  valid: boolean;
  duplicate: boolean;
  validation_error: string | null;
}

export interface ImportBatch {
  id: string;
  filename: string;
  state: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  error: string | null;
  created_at: string;
  preview: ImportRow[];
}

export interface Estimate {
  valid_contacts: number;
  healthy_accounts: number;
  contacts_per_account: Record<string, number>;
  effective_interval_minutes: number;
  estimated_start_at: string | null;
  estimated_finish_at: string | null;
  duration_minutes: number;
  spills_to_next_day: boolean;
  per_chip_daily_cap: number;
  daily_cap: number;
  daily_capacity: number;
  remaining_capacity_today: number;
  warnings: string[];
}

export interface Campaign {
  id: string;
  name: string;
  state: string;
  interval_mean_minutes: number;
  effective_interval_minutes: number | null;
  per_chip_daily_cap_snapshot: number;
  estimated_start_at: string | null;
  estimated_finish_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  review_required: number;
}

export interface ReviewItem {
  id: string;
  campaign_id: string;
  phone: string;
  account: string | null;
  started_at: string | null;
  last_error: string | null;
  state: string;
}

export interface RuntimeSettings {
  per_chip_daily_cap: number | null;
  business_start_hour: number;
  business_end_hour: number;
  timezone: string;
}

export interface SourceDatabaseSettings {
  server_old: string;
  database_old: string;
  username_old: string;
  password_configured: boolean;
  configured: boolean;
}

export interface MessageCardSettings {
  text: string;
  url: string;
  image_asset_id: string | null;
  image_url: string | null;
  show_url: boolean;
  revision: string;
  configured: boolean;
  updated_at: string | null;
}

export interface MessageGenerationSettings {
  api_key_configured: boolean;
  model: string;
}

export interface MessageVariationResponse {
  original: string;
  variations: string[];
}

export interface DashboardEvent {
  type: string;
  [key: string]: unknown;
}

export interface CampaignDraft {
  version: 1;
  batch_id: string | null;
  interval_mean_minutes: number;
  name: string;
  message: string;
  message_variations?: string[];
  message_variations_source?: string;
}

export interface RuntimeSettingsDraft extends RuntimeSettings {
  version: 1;
}

export interface MessageCardDraft {
  version: 1;
  text: string;
  url: string;
  show_url: boolean;
}

export interface SourceDatabaseDraft {
  version: 1;
  server_old: string;
  database_old: string;
  username_old: string;
}

export interface NewUserDraft {
  version: 1;
  email: string;
  role: Role;
}
