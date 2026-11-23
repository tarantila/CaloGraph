// Generated from public backend AI settings contracts. Do not edit by hand.

export const CONNECTION_PRIVACY_VERSION = "connection-metadata-2026-11-13" as const

export type AISettingsPut = {
  active_connection_id: string | null
  configuration_version: number | null
}

export type AISettingsView = {
  active_connection_id: string | null
}

export type ConnectionCheckPut = {
  configuration_version: number
  privacy_version: "connection-metadata-2026-11-13"
}

export type ConnectionCheckView = {
  id: string
  connection_id: string
  configuration_version: number
  privacy_version: string
  started_at: string
  completed_at: string | null
  status: "checking" | "interrupted" | "available" | "model_not_listed" | "manual_selection" | "authentication" | "rate_limited" | "destination_blocked" | "timeout" | "unavailable" | "invalid_response" | "unsupported" | "credential_unavailable"
  models: ReadonlyArray<string>
  models_truncated: boolean
  selected_model_available: boolean | null
  generation_verified?: false
}

export type ProviderCatalog = {
  providers: ReadonlyArray<ProviderDescriptor>
  privacy_version: string
  health_data_transmission_enabled?: false
}

export type ProviderConnectionPage = {
  items: ReadonlyArray<ProviderConnectionView>
  next_after: string | null
}

export type ProviderConnectionPut = {
  label: string
  provider_kind: "openai_api" | "anthropic" | "gemini" | "local" | "gateway"
  model_id?: string | null
  base_url?: string | null
  enabled?: boolean
  timeout_seconds?: number
  api_key?: string | null
}

export type ProviderConnectionView = {
  id: string
  label: string
  provider_kind: ProviderKind
  model_id: string | null
  base_url: string | null
  enabled: boolean
  timeout_seconds: number
  configuration_version: number
  has_credential: boolean
  created_at: string
  updated_at: string
}

export type ProviderDescriptor = {
  kind: ProviderKind
  availability: "available" | "planned"
  authentication: "api_key" | "optional_api_key" | "chatgpt_oauth"
  custom_endpoint: boolean
  model_discovery: boolean
  manual_model: boolean
  streaming?: boolean | null
  tool_calls?: boolean | null
  structured_output?: boolean | null
}

export type ProviderKind = "openai_api" | "chatgpt_plan" | "anthropic" | "gemini" | "local" | "gateway"
