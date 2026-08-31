export type Gender = 'female' | 'male' | 'non_binary'
export type DietType = 'no_special_diet' | 'pescetarian' | 'vegetarian' | 'vegan'

export type ProfileGenderValue = '' | Gender
export type ProfileDietValue = '' | DietType

type ProfileOption<T extends string> = Readonly<{
  value: T
  label: string
}>

export const PROFILE_GENDER_OPTIONS: readonly ProfileOption<ProfileGenderValue>[] = [
  { value: '', label: 'accountPersonal.notSpecified' },
  { value: 'female', label: 'accountPersonal.genderOptions.female' },
  { value: 'male', label: 'accountPersonal.genderOptions.male' },
  { value: 'non_binary', label: 'accountPersonal.genderOptions.non_binary' },
]

export const PROFILE_DIET_OPTIONS: readonly ProfileOption<ProfileDietValue>[] = [
  { value: '', label: 'accountPersonal.notSpecified' },
  { value: 'no_special_diet', label: 'accountPersonal.dietOptions.no_special_diet' },
  { value: 'pescetarian', label: 'accountPersonal.dietOptions.pescetarian' },
  { value: 'vegetarian', label: 'accountPersonal.dietOptions.vegetarian' },
  { value: 'vegan', label: 'accountPersonal.dietOptions.vegan' },
]

const GENDER_VALUES: Record<string, true> = { female: true, male: true, non_binary: true }
const DIET_VALUES: Record<string, true> = {
  no_special_diet: true,
  pescetarian: true,
  vegetarian: true,
  vegan: true,
}

/** Keep old persisted enum values readable while ensuring the next save clears them. */
export function normalizeGender(value: string | null | undefined): ProfileGenderValue {
  return value && GENDER_VALUES[value] ? value as Gender : ''
}

/** Keep old persisted enum values readable while ensuring the next save clears them. */
export function normalizeDietType(value: string | null | undefined): ProfileDietValue {
  return value && DIET_VALUES[value] ? value as DietType : ''
}
