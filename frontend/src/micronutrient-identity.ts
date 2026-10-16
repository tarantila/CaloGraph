import type { Component } from 'vue'
import {
  PhAcorn, PhAtom, PhAvocado, PhBone, PhBowlFood, PhBrain, PhCarrot, PhCircuitry, PhCow,
  PhDiamond, PhDna, PhDrop, PhEgg, PhFish, PhFlask, PhGearSix, PhGrains, PhLeaf,
  PhLightning, PhMagnet, PhOrangeSlice, PhPlant, PhShieldCheck, PhSun, PhTooth, PhWaves,
} from '@phosphor-icons/vue'

export const micronutrientIcons: Readonly<Partial<Record<string, Component>>> = {
  vitamin_a_ug: PhCarrot,
  thiamin_mg: PhGrains,
  riboflavin_mg: PhCow,
  niacin_mg: PhFish,
  pantothenic_acid_mg: PhBowlFood,
  vitamin_b6_mg: PhAvocado,
  biotin_ug: PhEgg,
  folate_ug: PhLeaf,
  vitamin_b12_ug: PhDna,
  vitamin_c_mg: PhOrangeSlice,
  vitamin_d_ug: PhSun,
  vitamin_e_mg: PhPlant,
  vitamin_k_ug: PhDrop,
  choline_mg: PhBrain,
  calcium_mg: PhBone,
  iron_mg: PhMagnet,
  potassium_mg: PhLightning,
  magnesium_mg: PhDiamond,
  phosphorus_mg: PhAtom,
  zinc_mg: PhShieldCheck,
  copper_mg: PhCircuitry,
  manganese_mg: PhGearSix,
  selenium_ug: PhAcorn,
  iodine_ug: PhWaves,
  fluoride_mg: PhTooth,
  chloride_mg: PhFlask,
}

export const micronutrientIdentityColorTokens: Readonly<Partial<Record<string, string>>> = {
  vitamin_a_ug: '--chart-series-emerald',
  folate_ug: '--analysis-micronutrient-lime',
  vitamin_c_mg: '--analysis-micronutrient-orange',
  vitamin_d_ug: '--analysis-micronutrient-yellow',
  calcium_mg: '--chart-series-blue',
  iron_mg: '--analysis-micronutrient-rust',
  potassium_mg: '--chart-series-violet',
  magnesium_mg: '--chart-series-teal',
}
export function micronutrientIconFor(metricKey: string): Component | undefined {
  return Object.hasOwn(micronutrientIcons, metricKey) ? micronutrientIcons[metricKey] : undefined
}

export function micronutrientColorTokenFor(metricKey: string): string | undefined {
  return Object.hasOwn(micronutrientIdentityColorTokens, metricKey)
    ? micronutrientIdentityColorTokens[metricKey] : undefined
}


let colorProbe: HTMLSpanElement | null = null

export function resolveMicronutrientIdentityColor(metricKey: string, fallback: string): string {
  const token = micronutrientColorTokenFor(metricKey) ?? '--chart-series-emerald'
  const color = getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback
  if (/^#(?:[\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i.test(color) || /^rgba?\(/i.test(color)) {
    return color
  }
  if (!document.body) return fallback

  colorProbe ??= document.createElement('span')
  colorProbe.style.color = color
  document.body.append(colorProbe)
  const resolved = getComputedStyle(colorProbe).color.trim()
  colorProbe.remove()
  if (/^rgba?\(/i.test(resolved)) return resolved
  const srgb = resolved.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)$/i)
  if (!srgb) return fallback
  const channels = srgb.slice(1).map((channel) => Math.round(Math.max(0, Math.min(1, Number(channel))) * 255))
  return `#${channels.map((channel) => channel.toString(16).padStart(2, '0')).join('')}`
}
