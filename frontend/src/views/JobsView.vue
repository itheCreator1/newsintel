<script setup lang="ts">
import { ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { api } from '../api'

const client = useQueryClient()
const stage = ref(''), status = ref(''), cursor = ref<string>()
const jobs = useQuery({ queryKey: ['jobs', stage, status, cursor], queryFn: () => api.jobs({ stage: stage.value || undefined, status: status.value || undefined, cursor: cursor.value }), refetchInterval: 5000 })
const backlog = useQuery({ queryKey: ['jobs-backlog'], queryFn: api.backlog, refetchInterval: 5000, retry: false })
const retry = useMutation({ mutationFn: api.retryJob, onSuccess: () => { client.invalidateQueries({ queryKey: ['jobs'] }); client.invalidateQueries({ queryKey: ['jobs-backlog'] }) } })
function setStage(event: Event) { cursor.value = undefined; stage.value = (event.target as HTMLSelectElement).value }
function setStatus(event: Event) { cursor.value = undefined; status.value = (event.target as HTMLSelectElement).value }
</script>

<template><header><div><p class="eyebrow">Article processing</p><h2>Jobs</h2></div></header>
  <p v-if="backlog.isPending.value" class="muted">Loading backlog…</p><p v-else-if="backlog.isError.value" class="error">Could not load backlog.</p><div v-else class="backlog"><span>Queued <strong>{{ backlog.data.value?.queued || 0 }}</strong></span><span>Running <strong>{{ backlog.data.value?.running || 0 }}</strong></span><span>Retrying <strong>{{ backlog.data.value?.retrying || 0 }}</strong></span><span>Failed <strong>{{ backlog.data.value?.failed || 0 }}</strong></span></div>
  <div class="job-filters"><label>Stage<select :value="stage" @change="setStage"><option value="">All stages</option><option value="fetch">Fetch</option><option value="extract">Extract</option></select></label><label>Status<select :value="status" @change="setStatus"><option value="">All statuses</option><option v-for="value in ['queued', 'running', 'retrying', 'succeeded', 'failed']" :key="value" :value="value">{{ value }}</option></select></label></div>
  <section class="panel jobs"><p v-if="jobs.isPending.value" class="muted">Loading processing jobs…</p><p v-else-if="jobs.isError.value" class="error">Could not load processing jobs.</p><p v-else-if="!jobs.data.value?.items.length" class="muted">No processing jobs.</p><p v-if="retry.isPending.value" class="muted">Retrying job…</p><p v-if="retry.isSuccess.value" class="success">Retry scheduled.</p><p v-if="retry.isError.value" class="error">Could not retry the job.</p><article v-for="job in jobs.data.value?.items" :key="job.id" class="job-row"><div><strong>{{ job.article_title }}</strong><small>{{ job.stage }} · {{ job.requested_mode.split('_').join(' ') }}</small></div><span :class="['badge', job.status === 'succeeded' ? 'healthy' : 'pending']">{{ job.status }}</span><p v-if="job.error_message" class="error">{{ job.error_category }}: {{ job.error_message }}</p><details v-if="job.attempts.length"><summary>{{ job.attempts.length }} attempts</summary><p v-for="attempt in job.attempts" :key="attempt.id" class="muted">{{ attempt.stage }} #{{ attempt.attempt_number }} — {{ attempt.status }}<span v-if="attempt.error_message">: {{ attempt.error_message }}</span></p></details><button v-if="job.status === 'failed'" class="secondary" :disabled="retry.isPending.value" @click="retry.mutate(job.id)">Retry</button></article><button v-if="jobs.data.value?.next_cursor" class="secondary" @click="cursor = jobs.data.value.next_cursor || undefined">Next page</button></section>
</template>
