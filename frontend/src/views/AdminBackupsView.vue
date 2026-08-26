<script setup lang="ts">
import {
  PhArrowSquareOut,
  PhDatabase,
  PhDesktop,
  PhEyeSlash,
  PhInfo,
  PhLockKey,
} from '@phosphor-icons/vue'

import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)

const backupFacts = [
  { key: 'method', label: 'backupUi.method', value: 'PostgreSQL + age', icon: PhDatabase },
  { key: 'management', label: 'backupUi.management', value: 'backupUi.hostManaged', icon: PhDesktop },
  { key: 'encryption', label: 'backupUi.encryption', value: 'age', icon: PhLockKey },
  { key: 'monitoring', label: 'backupUi.monitoring', value: 'backupUi.notMonitored', icon: PhEyeSlash },
] as const

function factValue(value: string): string {
  return value.startsWith('backupUi.') ? t(value) : value
}
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminUi.backupsTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.backupsDescription') }}</p>

    <section class="card admin-panel backup-overview" :aria-label="t('adminUi.backupsTitle')">
      <div class="backup-status-grid">
        <article v-for="fact in backupFacts" :key="fact.key" class="backup-status-item">
          <span class="backup-status-icon" aria-hidden="true">
            <component :is="fact.icon" :size="20" />
          </span>
          <div>
            <small>{{ t(fact.label) }}</small>
            <strong>{{ factValue(fact.value) }}</strong>
          </div>
        </article>
      </div>

      <div class="backup-retention">
        <div>
          <small>{{ t('backupUi.retention') }}</small>
          <strong>{{ t('backupUi.operatorPolicy') }}</strong>
        </div>
        <p>{{ t('backupUi.retentionRecommendation') }}</p>
      </div>

      <div class="backup-boundary-note">
        <PhInfo :size="20" aria-hidden="true" />
        <div>
          <strong>{{ t('backupUi.boundaryTitle') }}</strong>
          <p>{{ t('backupUi.boundaryDescription') }}</p>
          <p>{{ t('backupUi.lastRunUnknown') }}</p>
        </div>
      </div>

      <a
        class="button secondary compact-action backup-doc-link"
        href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md"
        target="_blank"
        rel="noopener noreferrer"
      >
        {{ t('adminUi.backupDocs') }}
        <PhArrowSquareOut :size="17" aria-hidden="true" />
      </a>
    </section>
  </section>
</template>
