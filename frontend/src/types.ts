export interface User {
  id: string
  username: string
  language: string
  timezone: string
  week_starts_on: number
  raw_payload_retention_days: number
  is_admin: boolean
  is_active: boolean
  deactivated_at: string | null
}

export type TrackingStatus =
  | 'complete'
  | 'probably_complete'
  | 'probably_incomplete'
  | 'incomplete'
  | 'no_data'

export type ActivityMode = 'off' | 'full'
export type ActivityDataStatus = 'disabled' | 'disabled_with_data' | 'missing' | 'credited'
export type ActivitySourceType =
  | 'yazio_export_v1'
  | 'apple_health_xml'
  | 'health_auto_export_v2'

export interface DailyPoint {
  date: string
  calories_kcal: number | null
  target_kcal: number | null
  maintenance_kcal: number | null
  deviation_kcal: number | null
  activity_mode: ActivityMode | null
  activity_source_type: ActivitySourceType | null
  active_energy_kcal: number | null
  activity_credit_kcal: number
  activity_data_status: ActivityDataStatus
  effective_budget_kcal: number | null
  effective_maintenance_kcal: number | null
  effective_deviation_kcal: number | null
  protein_g: number | null
  carbs_g: number | null
  fat_g: number | null
  tracking_status: TrackingStatus
  tracking_score: number
  tracking_reasons: string[]
  average_7d?: number | null
  average_14d?: number | null
  average_28d?: number | null
  classification?: string
}

export interface ImportBatch {
  id: string
  source_type: string
  client_identifier: string | null
  status: string
  started_at: string
  finished_at: string | null
  received: number
  inserted: number
  updated: number
  skipped: number
  failed: number
  unknown_types: string[]
  error_message: string | null
}

export interface ImportSummary {
  batch_id: string | null
  status: string
  received: number
  inserted: number
  updated: number
  skipped: number
  failed: number
  unknown_types: string[]
}

export interface YazioHistoricalSync {
  state: 'idle' | 'pending' | 'running' | 'completed' | 'failed'
  start_date: string | null
  end_date: string | null
  started_at: string | null
  completed_at: string | null
  last_error: string | null
}

export interface YazioStatus {
  available: boolean
  configured: boolean
  sync_enabled: boolean
  sync_interval_minutes: number | null
  sync_days: number | null
  sync_interval_override_minutes: number | null
  sync_days_override: number | null
  historical_sync: YazioHistoricalSync | null
  last_attempt_at: string | null
  last_success_at: string | null
  next_sync_at: string | null
  last_error: string | null
}

export type DecimalTransport = string | number | null

export interface Target {
  id: string
  valid_from: string
  valid_to: string | null
  calories_kcal: number
  maintenance_kcal: number | null
  protein_g: number
  carbs_g: number | null
  fat_g: number | null
  fiber_g: number | null
  target_weight_min_kg: DecimalTransport
  target_weight_max_kg: DecimalTransport
  activity_mode: ActivityMode
  activity_source_type: ActivitySourceType | null
}

export interface ApiProblem {
  type?: string
  title?: string
  status?: number
  detail?: string
  request_id?: string
}

export interface Achievement {
  key?: string | null
  category: string
  kind?: string | null
  icon?: string | null
  hidden: boolean
  placeholder: boolean
  unlocked: boolean
  unlocked_at?: string | null
  progress?: number | null
  target?: number | null
  sort_order: number
}

export interface AchievementListResponse {
  achievements: Achievement[]
}

export interface AchievementReconcileResponse extends AchievementListResponse {
  newly_unlocked: Achievement[]
}
