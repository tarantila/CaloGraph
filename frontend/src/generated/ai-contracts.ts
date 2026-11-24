// Generated from public backend AI settings and Expert contracts. Do not edit by hand.

export const CONNECTION_PRIVACY_VERSION = "connection-metadata-2026-11-13" as const

export const CONSENT_POLICY_VERSION = "expert-data-2026-11-14" as const

export type AIConsentPage = {
  items: ReadonlyArray<AIConsentView>
  next_after: string | null
}

export type AIConsentPut = {
  connection_id: string
  configuration_version: number
  purpose: "nutrition_chat" | "nutrition_check_in"
  data_domains: ReadonlyArray<"nutrition" | "targets" | "weight" | "activity" | "profile" | "health_context">
  include_meal_names: boolean
  history: ConsentHistory
  policy_version: "expert-data-2026-11-14"
}

export type AIConsentView = {
  id: string
  connection_id: string
  configuration_version: number
  provider_kind: ProviderKind
  model_id: string
  base_url?: string | null
  purpose: "nutrition_chat" | "nutrition_check_in"
  data_domains: ReadonlyArray<"nutrition" | "targets" | "weight" | "activity" | "profile" | "health_context">
  include_meal_names: boolean
  history: ConsentHistory
  policy_version: string
  state: "active" | "revoked" | "configuration_changed" | "connection_disabled"
  created_at: string
  revoked_at: string | null
}

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

export type ConsentHistory = {
  mode: "all_available" | "date_range"
  start: string | null
  end: string | null
}

export type ExpertPreferencesPut = {
  data_domains: ReadonlyArray<"nutrition" | "targets" | "weight" | "activity" | "profile" | "health_context">
  include_meal_names: boolean
}

export type ExpertPreferencesView = {
  data_domains: ReadonlyArray<"nutrition" | "targets" | "weight" | "activity" | "profile" | "health_context">
  include_meal_names: boolean
  configuration_version: number
}

export type NutritionDay = {
  date: string
  provider_key: string | null
  values: NutritionValues
}

export type NutritionDaysPage = {
  start: string
  end: string
  timezone: string
  days: ReadonlyArray<NutritionDay>
  evidence: ReadonlyArray<NutritionEvidence>
  next_after: string | null
}

export type NutritionDaysQuery = {
  start: string
  end: string
  page_size?: number
  after?: string | null
}

export type NutritionEvidence = {
  id: string
  date: string
  provider_key: string
  metric: "dietary_energy_kcal" | "protein_g" | "carbohydrates_g" | "fat_g" | "fiber_g" | "sugar_g" | "saturated_fat_g"
  value: number | string
  unit: "kcal" | "g"
}

export type NutritionValues = {
  dietary_energy_kcal: number | string | null
  protein_g: number | string | null
  carbohydrates_g: number | string | null
  fat_g: number | string | null
  fiber_g: number | string | null
  sugar_g: number | string | null
  saturated_fat_g: number | string | null
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
