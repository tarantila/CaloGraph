export const DEFAULT_NUTRITION_SECTION_ORDER = ['energy', 'macros', 'meals'] as const
export type NutritionSectionId = typeof DEFAULT_NUTRITION_SECTION_ORDER[number]

export function normalizeNutritionSectionOrder(value: unknown): NutritionSectionId[] {
  const order: NutritionSectionId[] = []
  if (Array.isArray(value)) {
    for (const id of value) {
      if (DEFAULT_NUTRITION_SECTION_ORDER.includes(id) && !order.includes(id)) order.push(id)
    }
  }
  for (const id of DEFAULT_NUTRITION_SECTION_ORDER) {
    if (!order.includes(id)) order.push(id)
  }
  return order
}

export function nutritionSectionOrderStorageKey(userId: string): string {
  return `calograph.analysis.nutrition.section-order.v1.${encodeURIComponent(userId)}`
}

export function moveNutritionSection(
  order: readonly NutritionSectionId[], id: NutritionSectionId, direction: -1 | 1,
): NutritionSectionId[] {
  const result = normalizeNutritionSectionOrder(order)
  const index = result.indexOf(id)
  const target = index + direction
  if (index < 0 || target < 0 || target >= result.length) return result
  result.splice(index, 1)
  result.splice(target, 0, id)
  return result
}

export interface NutritionMealOccurrenceEntry {
  id: string
  time: string | null
  type: string | null
  name: string | null
  energy: number | null
}

export interface NutritionMealOccasion<T extends NutritionMealOccurrenceEntry = NutritionMealOccurrenceEntry> {
  key: string
  type: string | null
  time: string | null
  entries: T[]
  entryCount: number
  totalEnergy: number | null
}

function compareMealTime(a: string | null, b: string | null): number {
  if (a === null) return b === null ? 0 : 1
  if (b === null) return -1
  return a.localeCompare(b)
}

export function groupNutritionMealOccasions<T extends NutritionMealOccurrenceEntry>(
  entries: readonly T[],
): NutritionMealOccasion<T>[] {
  const groups = new Map<string, { key: string; type: string | null; entries: T[] }>()
  for (const entry of entries) {
    const type = entry.type?.trim() || null
    const key = type ? `type:${type}` : `entry:${entry.id}`
    let occasion = groups.get(key)
    if (!occasion) {
      occasion = { key, type, entries: [] }
      groups.set(key, occasion)
    }
    occasion.entries.push(entry)
  }

  return [...groups.values()].map(({ key, type, entries: occasionEntries }) => {
    const sortedEntries = [...occasionEntries].sort((a, b) => compareMealTime(a.time, b.time))
    const time = sortedEntries.find((entry) => entry.time !== null)?.time ?? null
    const totalEnergy = sortedEntries.length > 1
      && sortedEntries.every((entry) => entry.energy !== null && Number.isFinite(entry.energy))
      ? sortedEntries.reduce((total, entry) => total + entry.energy!, 0)
      : null
    return { key, type, time, entries: sortedEntries, entryCount: sortedEntries.length, totalEnergy }
  }).sort((a, b) => compareMealTime(a.time, b.time) || a.key.localeCompare(b.key))
}

interface NamedMealInput {
  event_id: string
  local_time: string | null
  meal_type: string | null
  display_name: string | null
  dietary_energy_kcal: number | null
}

export interface RecentNutritionMeal extends NutritionMealOccurrenceEntry {
  eventId: string
  date: string
}

interface NutritionMealDateGroup {
  date: string
  occasions: NutritionMealOccasion<RecentNutritionMeal>[]
}

export function projectNutritionMeals(
  days: readonly { date: string; meals: readonly NamedMealInput[] }[],
): { preview: NutritionMealDateGroup[]; all: NutritionMealDateGroup[]; hasMore: boolean } {
  const candidates = days.flatMap((day) => {
    const entries = day.meals.map((meal) => ({
      id: meal.event_id,
      eventId: meal.event_id,
      date: day.date,
      time: meal.local_time || null,
      type: meal.meal_type,
      name: meal.display_name?.trim() || null,
      energy: meal.dietary_energy_kcal,
    }))
    return groupNutritionMealOccasions(entries)
      .filter((occasion) => occasion.entries.some((entry) => entry.name !== null))
      .map((occasion) => ({
        ...occasion,
        entries: occasion.entries.filter((entry) => entry.name !== null),
        date: day.date,
      }))
  })

  const latestOccasions = candidates.sort((a, b) =>
    b.date.localeCompare(a.date)
    || (a.time === null ? (b.time === null ? 0 : 1) : b.time === null ? -1 : b.time.localeCompare(a.time))
    || a.key.localeCompare(b.key),
  ).slice(0, 6)
  const visibleKeys = new Set(latestOccasions.map((occasion) => `${occasion.date}\u0000${occasion.key}`))
  candidates.sort((a, b) => b.date.localeCompare(a.date) || compareMealTime(a.time, b.time) || a.key.localeCompare(b.key))
  const all: NutritionMealDateGroup[] = []
  const preview: NutritionMealDateGroup[] = []
  for (const occasion of candidates) {
    let group = all.at(-1)
    if (!group || group.date !== occasion.date) {
      group = { date: occasion.date, occasions: [] }
      all.push(group)
    }
    group.occasions.push(occasion)
    if (!visibleKeys.has(`${occasion.date}\u0000${occasion.key}`)) continue
    let previewGroup = preview.at(-1)
    if (!previewGroup || previewGroup.date !== occasion.date) {
      previewGroup = { date: occasion.date, occasions: [] }
      preview.push(previewGroup)
    }
    previewGroup.occasions.push(occasion)
  }
  return { preview, all, hasMore: candidates.length > 6 }
}
