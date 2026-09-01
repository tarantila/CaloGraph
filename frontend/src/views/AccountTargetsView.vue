<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'

import { PhTrash } from '@phosphor-icons/vue'

import { api, ApiError, localizeApiError } from '../api'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DateInput from '../components/DateInput.vue'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  displayTargetWeight,
  formatTargetWeight,
  kgToWeight,
  saveTargetDraft,
  targetWeightFromTarget,
  targetWeightPayload,
  TARGET_LIMITS,
  TARGET_WEIGHT_LIMITS,
  TargetValidationError,
  weightToKg,
} from '../target-form'
import { useProfilePreferences } from '../composables/useProfilePreferences'
import type { ActivitySourceType, Target } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const target = reactive(createEmptyTargetDraft())
const targets = ref<Target[]>([])
const activitySources = ref<ActivitySourceType[]>([])
const targetToDelete = ref<Target | null>(null)
const deletingTarget = ref(false)
const targetDeleteError = ref('')
const message = ref('')
const error = ref('')
const weightError = ref('')
const loading = ref(true)
const loaded = ref(false)
const savingTarget = ref(false)
const profilePreferences = useProfilePreferences()
let loadGeneration = 0

const preferredWeightUnit = computed(() => profilePreferences.profile.preferred_weight_unit)

function weightInputToKg(value: unknown): number | null {
  if (value === '' || value == null) return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? weightToKg(numeric, preferredWeightUnit.value) : null
}

const targetWeightMode = computed({
  get: () => target.target_weight_mode,
  set: (mode: 'none' | 'exact' | 'range') => {
    target.target_weight_mode = mode
    weightError.value = ''
    if (mode === 'none') {
      target.target_weight_min_kg = null
      target.target_weight_max_kg = null
    } else if (mode === 'exact') {
      const value = target.target_weight_min_kg ?? target.target_weight_max_kg
      target.target_weight_min_kg = value
      target.target_weight_max_kg = value
    }
  },
})

const targetWeightExact = computed({
  get: () => target.target_weight_min_kg == null ? null : displayTargetWeight(Number(target.target_weight_min_kg), preferredWeightUnit.value),
  set: (value: unknown) => {
    const kg = weightInputToKg(value)
    target.target_weight_min_kg = kg
    target.target_weight_max_kg = kg
    weightError.value = ''
  },
})

const targetWeightMin = computed({
  get: () => target.target_weight_min_kg == null ? null : displayTargetWeight(Number(target.target_weight_min_kg), preferredWeightUnit.value),
  set: (value: unknown) => {
    target.target_weight_min_kg = weightInputToKg(value)
    weightError.value = ''
  },
})

const targetWeightMax = computed({
  get: () => target.target_weight_max_kg == null ? null : displayTargetWeight(Number(target.target_weight_max_kg), preferredWeightUnit.value),
  set: (value: unknown) => {
    target.target_weight_max_kg = weightInputToKg(value)
    weightError.value = ''
  },
})

const integer = createNumberFormatter({ maximumFractionDigits: 0 })

const targetDeleteDescription = computed(() => {
  const item = targetToDelete.value
  if (!item) return ''
  const status = item.valid_to == null ? t('settingsUi.current') : t('settingsUi.historical')
  return t('settingsUi.targetDeleteDescription', {
    date: formatGermanDate(item.valid_from),
    status,
  })
})

const selectableActivitySources = computed(() => {
  const sourceTypes = new Set(activitySources.value)
  if (target.activity_source_type) sourceTypes.add(target.activity_source_type)
  return [...sourceTypes].sort()
})

const activityEnabled = computed({
  get: () => target.activity_mode === 'full',
  set: (enabled: boolean) => {
    target.activity_mode = enabled ? 'full' : 'off'
    if (!enabled) target.activity_source_type = null
  },
})

function activityHistoryLabel(item: Target) {
  return item.activity_mode === 'full'
    ? t('activity.historyEnabled', { source: activitySourceLabel(item.activity_source_type) })
    : t('activity.historyDisabled')
}

function activitySourceLabel(sourceType: ActivitySourceType | null) {
  if (!sourceType) return '–'
  return t(`activity.source.${sourceType}`)
}

async function loadTargets() {
  const [targetResult, sourceResult] = await Promise.all([
    api<Target[]>('/settings/targets'),
    api<Array<{ source_type: ActivitySourceType }>>('/settings/activity-sources'),
  ])
  targetResult.forEach((item) => targetWeightFromTarget(item))
  targets.value = targetResult
  activitySources.value = Array.isArray(sourceResult)
    ? sourceResult.map((item) => item.source_type)
    : []
  const currentTarget = targetResult.find((item) => item.valid_to == null) ?? targetResult[0]
  if (currentTarget) {
    target.calories_kcal = Number(currentTarget.calories_kcal)
    target.maintenance_kcal = currentTarget.maintenance_kcal == null ? null : Number(currentTarget.maintenance_kcal)
    target.activity_mode = currentTarget.activity_mode ?? 'off'
    target.activity_source_type = currentTarget.activity_source_type ?? null
    target.protein_g = Number(currentTarget.protein_g)
    target.carbs_g = currentTarget.carbs_g == null ? null : Number(currentTarget.carbs_g)
    target.fat_g = currentTarget.fat_g == null ? null : Number(currentTarget.fat_g)
    target.fiber_g = currentTarget.fiber_g == null ? null : Number(currentTarget.fiber_g)
    const weight = targetWeightFromTarget(currentTarget)
    target.target_weight_mode = weight.mode
    target.target_weight_min_kg = weight.minKg
    target.target_weight_max_kg = weight.maxKg
  } else {
    target.target_weight_mode = 'none'
    target.target_weight_min_kg = null
    target.target_weight_max_kg = null
  }
  target.valid_from = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
}

function targetWeightHistoryLabel(item: Target) {
  const weight = targetWeightFromTarget(item)
  if (weight.mode === 'none') return t('settingsUi.targetWeightHistoryNone')
  const unit = preferredWeightUnit.value
  if (weight.mode === 'exact') {
    return t('settingsUi.targetWeightHistoryExact', {
      value: formatTargetWeight(Number(weight.minKg), unit),
      unit,
    })
  }
  return t('settingsUi.targetWeightHistoryRange', {
    min: formatTargetWeight(Number(weight.minKg), unit),
    max: formatTargetWeight(Number(weight.maxKg), unit),
    unit,
  })
}

async function load() {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const [, preferencesLoaded] = await Promise.all([loadTargets(), profilePreferences.load()])
    if (!preferencesLoaded) return
    loaded.value = true
  } catch (cause) {
    if (generation !== loadGeneration) return
    error.value = cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.loadFailed') : t('settingsUi.loadFailed')
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function openTargetDelete(item: Target) {
  if (targets.value.length <= 1) return
  targetDeleteError.value = ''
  targetToDelete.value = item
}

function closeTargetDelete() {
  if (!deletingTarget.value) {
    targetToDelete.value = null
    targetDeleteError.value = ''
  }
}

async function confirmTargetDelete() {
  const item = targetToDelete.value
  if (!item) return
  deletingTarget.value = true
  targetDeleteError.value = ''
  try {
    await api<void>(`/settings/targets/${item.valid_from}`, { method: 'DELETE' })
    await loadTargets()
    targetToDelete.value = null
    message.value = t('settingsUi.targetDeleted')
  } catch (cause) {
    targetDeleteError.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.targetDeleteFailed')
        : t('settingsUi.targetDeleteFailed')
  } finally {
    deletingTarget.value = false
  }
}

function targetDeleteLabel(item: Target) {
  return targets.value.length <= 1
    ? t('settingsUi.targetDeleteUnavailable', { date: formatGermanDate(item.valid_from) })
    : t('settingsUi.deleteTarget', { date: formatGermanDate(item.valid_from) })
}

async function saveTarget() {
  error.value = ''
  message.value = ''
  weightError.value = ''
  savingTarget.value = true
  try {
    targetWeightPayload(target, 'kg')
    await saveTargetDraft(target, targets.value)
    message.value = t('settingsUi.targetSaved', { date: formatGermanDate(target.valid_from) })
    auth.completeTargetSetup()
    await loadTargets()
  } catch (cause) {
    if (cause instanceof TargetValidationError) weightError.value = cause.message
    error.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.targetSaveFailed')
        : cause instanceof TargetValidationError
          ? cause.message
          : t('settingsUi.targetSaveFailed')
  } finally {
    savingTarget.value = false
  }
}

onBeforeUnmount(() => {
  ++loadGeneration
})

void load()
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('settings.targets') }}</h1>
      <p>{{ t('settings.targetsDescription') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('settingsUi.loading') }}</div>
  <template v-else>
    <div v-if="error" class="card error" role="alert" aria-live="assertive">{{ error }}</div>
    <p v-if="message" role="status" aria-live="polite">{{ message }}</p>

    <div v-if="loaded" class="content-grid">
      <section class="card form-card">
        <h2>{{ t('settingsUi.targetsTitle') }}</h2>
        <p v-if="!targets.length" class="setup-notice">
          {{ t('settingsUi.initialTargetsDescription') }}
        </p>
        <form class="form-grid" @submit.prevent="saveTarget">
          <label class="field">{{ t('settingsUi.validFrom') }}<DateInput v-model="target.valid_from" required /></label>
          <label class="field">{{ t('settingsUi.calorieBudget') }}<input v-model.number="target.calories_kcal" type="number" :min="TARGET_LIMITS.caloriesMin" step="1" required /></label>
          <label class="field">{{ t('settingsUi.maintenance') }}<input v-model.number="target.maintenance_kcal" type="number" :min="TARGET_LIMITS.maintenanceMin" step="0.001" /><small>{{ t('settingsUi.maintenanceHelp') }}</small></label>
          <fieldset class="macro-target-settings full">
            <legend>{{ t('settingsUi.macroTargets') }}</legend>
            <div class="macro-target-grid">
              <label class="field">{{ t('settingsUi.proteinTarget') }}<input v-model.number="target.protein_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" required /></label>
              <label class="field">{{ t('settingsUi.carbsTarget') }}<input v-model.number="target.carbs_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
              <label class="field">{{ t('settingsUi.fatTarget') }}<input v-model.number="target.fat_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
              <label class="field">{{ t('settingsUi.fiberTarget') }}<input v-model.number="target.fiber_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            </div>
          </fieldset>
          <fieldset class="field full settings-subsection activity-target-settings">
            <legend class="activity-card-header">
              <span>{{ t('activity.title') }}</span>
              <span :class="['activity-status-badge', { active: activityEnabled }]">{{ activityEnabled ? t('activity.statusActive') : t('activity.statusDisabled') }}</span>
            </legend>
            <p class="activity-description">{{ t('activity.description') }}</p>
            <label class="activity-toggle-row">
              <span>{{ t('activity.enabled') }}</span>
              <input v-model="activityEnabled" type="checkbox" role="switch" :disabled="!selectableActivitySources.length" />
            </label>
            <div v-if="activityEnabled" class="activity-source-settings">
              <label class="field">
                {{ t('activity.sourceLabel') }}
                <select v-model="target.activity_source_type" name="activity-source" required>
                  <option :value="null" disabled>{{ t('activity.sourcePlaceholder') }}</option>
                  <option v-for="sourceType in selectableActivitySources" :key="sourceType" :value="sourceType">
                    {{ activitySourceLabel(sourceType) }}
                  </option>
                </select>
              </label>
            </div>
            <small v-if="!selectableActivitySources.length" class="activity-source-unavailable">
              {{ t('activity.noSources') }}
            </small>
          </fieldset>
          <fieldset class="field full settings-subsection target-weight-settings" :aria-describedby="weightError ? 'target-weight-error' : undefined">
            <legend>{{ t('settingsUi.targetWeight') }}</legend>
            <p class="target-weight-description">{{ t('settingsUi.targetWeightDescription') }}</p>
            <div class="target-weight-mode-options" role="radiogroup" :aria-label="t('settingsUi.targetWeightMode')">
              <label>
                <input v-model="targetWeightMode" type="radio" name="target-weight-mode" value="none" />
                {{ t('settingsUi.targetWeightNone') }}
              </label>
              <label>
                <input v-model="targetWeightMode" type="radio" name="target-weight-mode" value="exact" />
                {{ t('settingsUi.targetWeightExact') }}
              </label>
              <label>
                <input v-model="targetWeightMode" type="radio" name="target-weight-mode" value="range" />
                {{ t('settingsUi.targetWeightRange') }}
              </label>
            </div>
            <label v-if="targetWeightMode === 'exact'" class="field">
              {{ t('settingsUi.targetWeight') }} ({{ preferredWeightUnit }})
              <input v-model.number="targetWeightExact" name="target-weight-exact" type="number" min="0" :max="kgToWeight(TARGET_WEIGHT_LIMITS.maxKg, preferredWeightUnit)" :step="preferredWeightUnit === 'lb' ? 0.1 : 0.001" required />
            </label>
            <div v-if="targetWeightMode === 'range'" class="target-weight-range">
              <label class="field">
                {{ t('settingsUi.targetWeightFrom') }} ({{ preferredWeightUnit }})
                <input v-model.number="targetWeightMin" name="target-weight-min" type="number" min="0" :max="kgToWeight(TARGET_WEIGHT_LIMITS.maxKg, preferredWeightUnit)" :step="preferredWeightUnit === 'lb' ? 0.1 : 0.001" required />
              </label>
              <label class="field">
                {{ t('settingsUi.targetWeightTo') }} ({{ preferredWeightUnit }})
                <input v-model.number="targetWeightMax" name="target-weight-max" type="number" min="0" :max="kgToWeight(TARGET_WEIGHT_LIMITS.maxKg, preferredWeightUnit)" :step="preferredWeightUnit === 'lb' ? 0.1 : 0.001" required />
              </label>
            </div>
            <p v-if="weightError" id="target-weight-error" class="field-error" role="alert">{{ weightError }}</p>
          </fieldset>
          <button class="button compact-action" type="submit" :disabled="savingTarget">
            {{ savingTarget ? t('settingsUi.saving') : t('settingsUi.saveTargets') }}
          </button>
        </form>
      </section>

      <section class="card form-card budget-help">
        <h2>{{ t('settingsUi.budgetHelpTitle') }}</h2>
        <p>{{ t('settingsUi.budgetHelpBudget') }}</p>
        <p>{{ t('settingsUi.budgetHelpMaintenance') }}</p>
        <p>{{ t('settingsUi.budgetHelpCalendar') }}</p>
        <p>{{ t('settingsUi.budgetHelpValidFrom') }}</p>
      </section>
    </div>

    <section v-if="loaded" class="card table-card">
      <div class="section-card-header">
        <div><h2>{{ t('settingsUi.historyTitle') }}</h2><p>{{ t('settingsUi.historyDescription') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ t('settingsUi.validFrom') }}</th><th>{{ t('common.to') }}</th><th class="number">{{ t('settingsUi.calorieBudget') }}</th><th class="number">{{ t('settingsUi.maintenance') }}</th><th>{{ t('activity.title') }}</th><th>{{ t('settingsUi.targetWeight') }}</th><th class="number">{{ t('settingsUi.proteinTarget') }}</th><th class="actions">{{ t('common.actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in targets" :key="item.id">
              <td>{{ formatGermanDate(item.valid_from) }}</td>
              <td>{{ item.valid_to ? formatGermanDate(item.valid_to) : t('settingsUi.current') }}</td>
              <td class="number">{{ integer.format(Number(item.calories_kcal)) }} {{ t('common.kcal') }}</td>
              <td class="number">{{ item.maintenance_kcal == null ? '–' : `${integer.format(Number(item.maintenance_kcal))} ${t('common.kcal')}` }}</td>
              <td>{{ activityHistoryLabel(item) }}</td>
              <td>{{ targetWeightHistoryLabel(item) }}</td>
              <td class="number">{{ integer.format(Number(item.protein_g)) }} {{ t('common.grams') }}</td>
              <td class="actions">
                <button
                  class="icon-button danger"
                  type="button"
                  :aria-label="targetDeleteLabel(item)"
                  :title="targetDeleteLabel(item)"
                  :disabled="targets.length <= 1"
                  @click="openTargetDelete(item)"
                >
                  <PhTrash :size="18" weight="duotone" aria-hidden="true" />
                </button>
              </td>
            </tr>
            <tr v-if="!targets.length"><td colspan="8" class="empty">{{ t('settingsUi.noTargets') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <ConfirmDialog
      v-if="loaded && targetToDelete !== null"
      :open="true"
      :title="t('settingsUi.targetDeleteTitle')"
      :description="targetDeleteDescription"
      :confirm-label="t('common.delete')"
      :danger="true"
      :pending="deletingTarget"
      :error="targetDeleteError"
      @confirm="confirmTargetDelete"
      @close="closeTargetDelete"
    />
  </template>
</template>
