export interface User {
  id: string
  username: string
  language: string
  timezone: string
  week_starts_on: number
  preferred_weight_unit: 'kg' | 'lb'
  raw_payload_retention_days: number
}

export type TrackingStatus =
  | 'complete'
  | 'probably_complete'
  | 'probably_incomplete'
  | 'incomplete'
  | 'no_data'

export interface DailyPoint {
  date: string
  calories_kcal: number | null
  target_kcal: number | null
  deviation_kcal: number | null
  protein_g: number | null
  carbs_g: number | null
  fat_g: number | null
  active_energy_kcal: number | null
  steps: number | null
  weight_kg: number | null
  tracking_status: TrackingStatus
  tracking_score: number
  tracking_reasons: string[]
  average_7d?: number | null
  average_14d?: number | null
  average_28d?: number | null
  weight_average_7d?: number | null
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

export interface Target {
  id: string
  valid_from: string
  valid_to: string | null
  calories_kcal: number
  protein_g: number
  carbs_g: number | null
  fat_g: number | null
  fiber_g: number | null
  water_ml: number | null
}

export interface ApiProblem {
  detail: string
  request_id?: string
}
