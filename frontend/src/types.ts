export interface User {
  id: string
  username: string
  language: string
  timezone: string
  week_starts_on: number
  preferred_weight_unit?: string
  raw_payload_retention_days: number
  highlight_over_budget?: boolean
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
  | 'google_health_activity_v4'
  | 'withings_activity_v2'

export type VerificationView = 'canonical' | 'all'
export type VerificationProviderKey = 'google_health' | 'yazio' | 'apple_health'
export type VerificationActivityProviderKey = VerificationProviderKey | 'withings'
export type VerificationRecordStatus = 'available' | 'no_data' | 'unavailable'
export type VerificationActivitySourceType =
  | 'google_health_activity_v4'
  | 'yazio_export_v1'
  | 'apple_health_xml'
  | 'health_auto_export_v2'
  | 'withings_activity_v2'

export interface VerificationActivityProviderRecord {
  provider_key: VerificationActivityProviderKey
  status: VerificationRecordStatus
  active_energy_kcal: number | null
  record_count: number
  source_types: VerificationActivitySourceType[]
}

export interface VerificationActivityDayResponse {
  date: string
  canonical: VerificationActivityProviderRecord | null
  providers: VerificationActivityProviderRecord[]
}

export interface VerificationActivityResponse {
  start_date: string
  end_date: string
  view: VerificationView
  days: VerificationActivityDayResponse[]
}

export interface VerificationNutritionSummary {
  calories_kcal: number | null
  protein_g: number | null
  carbohydrates_g: number | null
  fat_g: number | null
}

export interface VerificationNutritionEvent {
  provider_key: VerificationProviderKey
  local_time: string | null
  meal_type: string | null
  display_name: string
  calories_kcal: number | null
  protein_g: number | null
  carbohydrates_g: number | null
  fat_g: number | null
  serving_amount: number | null
  serving_unit: string | null
}

export interface VerificationNutritionProviderGroup {
  provider_key: VerificationProviderKey
  status: VerificationRecordStatus
  record_count: number
  summary: VerificationNutritionSummary
  events: VerificationNutritionEvent[]
}

export interface VerificationNutritionResponse {
  date: string
  view: VerificationView
  canonical: VerificationNutritionProviderGroup | null
  providers: VerificationNutritionProviderGroup[]
}
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
export interface WeightPoint {
  date: string
  weight_kg: number
}

export interface WeightResponse {
  start_date: string
  end_date: string
  selected_provider: { provider_key: string } | null
  points: WeightPoint[]
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
  scheduler_enabled: boolean
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

export type GoogleHealthState = 'disabled' | 'not_configured' | 'not_connected' | 'active' | 'reauth_required' | 'scope_missing'
export type GoogleHealthSyncState = 'idle' | 'running' | 'completed' | 'failed'
export type GoogleHealthConnectionTestStatus = 'connected' | 'reauth_required' | 'failed'

export interface GoogleHealthStatus {
  available: boolean
  configured: boolean
  client_id_configured: boolean
  client_secret_configured: boolean
  redirect_uri: string
  state: GoogleHealthState
  sync_state: GoogleHealthSyncState
  retry_attempt: number
  retry_max_attempts: number
  next_retry_at: string | null
  granted_scopes: string[]
  refresh_token_expires_at: string | null
  last_attempt_at: string | null
  last_success_at: string | null
  last_error: string | null
  last_error_category: string | null
}

export type GoogleHealthDomainKey = 'nutrition' | 'activity_energy' | 'weight'
export type GoogleHealthAggregateStatus = 'success' | 'partial_failure' | 'reauth_required' | 'failed' | 'no_data'
export type GoogleHealthDomainStatus = 'success' | 'truncated' | 'no_data' | 'failed' | 'reauth_required'

export interface GoogleHealthDomainDiagnostic {
  domain: string
  operation: string
  endpoint_key: string
  parser_stage: string
  structural_reason_code: string | null
  error_category: string
  upstream_status_code: number | null
  retryable: boolean
  reauth_required: boolean
  chunk_index?: number | null
  page_index?: number | null
  point_index?: number | null
  field_path?: string | null
  validation_rule?: string | null
  numeric_reason_code?: string | null
  observed_json_type?: string | null
  expected_json_type?: string | null
}

export interface GoogleHealthDomainResult {
  status: GoogleHealthDomainStatus
  fetched_count: number
  persisted_count: number
  requested_start: string
  requested_end: string
  covered_start: string | null
  covered_end: string | null
  error_code: string | null
  diagnostic?: GoogleHealthDomainDiagnostic | null
}

export interface GoogleHealthSyncResult {
  status: GoogleHealthAggregateStatus
  nutrition: GoogleHealthDomainResult
  activity_energy: GoogleHealthDomainResult
  weight: GoogleHealthDomainResult
}

export type WithingsState = 'disabled' | 'not_configured' | 'not_connected' | 'active' | 'reauth_required' | 'error'
export type WithingsSyncStatus = 'success' | 'partial_failure' | 'failed' | 'reauth_required' | 'no_data'
export type WithingsDomainKey = 'weight' | 'activity_energy'

export interface WithingsStatus {
  available: boolean
  configured: boolean
  credentials_configured: boolean
  redirect_uri: string
  connected: boolean
  state: WithingsState
  granted_scopes: string[]
  access_token_expires_at: string | null
  last_attempt_at: string | null
  last_success_at: string | null
  last_error_category: string | null
}

export interface WithingsConnectionTestResponse {
  ok: boolean
  state: WithingsState
  error_category: string | null
}

export interface WithingsDomainResult {
  status: string
  fetched_count: number
  persisted_count: number
  requested_start: string
  requested_end: string
  covered_start: string | null
  covered_end: string | null
  error_code: string | null
}

export interface WithingsSyncResult {
  status: WithingsSyncStatus
  weight: WithingsDomainResult
  activity_energy: WithingsDomainResult
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

export type OnboardingStep = 'personal' | 'targets' | 'security' | 'completed'
export type OnboardingMode = 'full' | 'legacy'

export interface OnboardingStatus {
  mode: OnboardingMode
  required: boolean
  completed: boolean
  current_step: OnboardingStep
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
export type BackupHealthState = 'healthy' | 'attention' | 'failed' | 'unknown' | 'disabled'
export type RestoreTestState = 'never_tested' | 'current' | 'due' | 'unknown' | 'failed'

export interface BackupComponentStatus {
  state?: BackupHealthState
  verification?: 'full' | 'checksum' | 'not_verified' | 'not_reported'
  encryption?: 'age'
  matching_backup?: boolean
  off_host_copy?: boolean
  immutable_copy?: boolean
  last_success_at?: string
  last_attempt_at?: string
  last_verified_at?: string
  artifact_created_at?: string
  last_restore_test_at?: string
  age_seconds?: number
}

export interface ArchiveVerificationStatus {
  state: 'verified' | 'not_verified' | 'unknown' | 'disabled'
  verified_at?: string
  latest_artifact_verified?: boolean
}

export interface RestoreTestStatus {
  state: RestoreTestState
  result?: 'NEVER_TESTED' | 'RESTORE_TESTED' | 'RESTORE_TEST_FAILED'
  tested_at?: string
  last_success_at?: string
  next_due_at?: string
  postgres_major?: number
  off_host_copy?: boolean
  immutable_copy?: boolean
  failure_code?: string
  reason?: string
}

export interface BackupRecoveryStatus {
  overall_state: BackupHealthState
  archive_verification: {
    overall_state: BackupHealthState
    components: {
      database?: ArchiveVerificationStatus
      environment_secrets?: ArchiveVerificationStatus
    }
  }
  restore_test: RestoreTestStatus
}

export interface BackupAutomationStatus {
  enabled?: boolean
  last_attempt_at?: string
  last_success_at?: string
  next_run_at?: string
  last_error_code?: string | null
  schedule_timezone?: string
  schedule_time?: string
  retention_days?: number
}

export interface BackupStatus {
  schema_version: 1
  reported_at?: string
  target?: string
  freshness_threshold_seconds?: number
  overall_state: BackupHealthState
  reason_codes: string[]
  automation?: BackupAutomationStatus
  components?: {
    database?: BackupComponentStatus
    environment_secrets?: BackupComponentStatus
  }
  recovery?: BackupRecoveryStatus
}
