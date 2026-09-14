<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { api } from '../api'
import type { StopWords } from '../api-types'

const client = useQueryClient()
const draft = ref<string | null>(null)
const stopWords = ref<StopWords>()
const stopWordsError = ref(false)
onMounted(async () => { try { stopWords.value = await api.stopWords(); draft.value = stopWords.value.words.join('\n') } catch { stopWordsError.value = true } })
const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, retry: false })
const save = useMutation({
  mutationFn: () => api.updateStopWords(stopWords.value!.revision, [...new Set((draft.value ?? '').split(/\r?\n/).map(word => word.trim().toLocaleLowerCase('en')).filter(Boolean))]),
  onSuccess: data => { stopWords.value = data; draft.value = data.words.join('\n'); client.invalidateQueries({ queryKey: ['nlp-status'] }) },
})
</script>

<template>
  <header><div><p class="eyebrow">NLP configuration</p><h2>Settings</h2></div></header>
  <section class="panel settings-panel"><h3>Processor capabilities</h3><p v-if="nlp.isPending.value" class="muted">Loading capabilities…</p><p v-else-if="nlp.isError.value" class="error">Could not load processor capabilities.</p><p v-for="capability in nlp.data.value?.capabilities" :key="capability.name" class="muted">{{ capability.name }} · {{ capability.state }}<span v-if="capability.version"> · {{ capability.version }}</span><span v-if="capability.detail"> · {{ capability.detail }}</span></p></section>
  <section class="panel settings-panel"><h3>English stop words</h3><p>Changes affect new processing immediately. Existing annotations require reprocessing before they reflect this revision.</p><p v-if="!stopWords && !stopWordsError" class="muted">Loading stop words…</p><p v-else-if="stopWordsError" class="error">Could not load stop words.</p><form v-else @submit.prevent="save.mutate()"><label>English stop words<textarea v-model="draft" rows="18" spellcheck="false"></textarea></label><p class="muted">One word per line · revision {{ stopWords?.revision }}</p><button type="submit" :disabled="save.isPending.value">Save stop words</button></form><p v-if="save.isSuccess.value" class="success">Stop words saved as revision {{ save.data.value?.revision }}.</p><p v-if="save.isError.value" class="error">Could not save stop words. Reload the current revision and try again.</p></section>
</template>
