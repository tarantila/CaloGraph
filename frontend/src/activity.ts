import type { DailyPoint } from './types'

type ActivityPoint = Pick<DailyPoint, 'activity_data_status' | 'activity_credit_kcal'>

export function hasActivityCredit(point: ActivityPoint): boolean {
  return point.activity_data_status === 'credited' && Number(point.activity_credit_kcal) > 0
}

export function hasActivityCreditAmount(activityCreditKcal: unknown): boolean {
  return Number(activityCreditKcal) > 0
}
