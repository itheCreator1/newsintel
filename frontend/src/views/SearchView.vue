<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'

const route = useRoute(), router = useRouter()
const read = (key: string) => typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const readMany = (key: string) => {
  const value = route.query[key]
  return (Array.isArray(value) ? value : value ? [value] : []).filter((item): item is string => typeof item === 'string')
}
const routeForm = () => ({
  q: read('q'), source_id: readMany('source_id'), country: read('country'), after: read('after'), before: read('before'),
  content_available: read('content_available'), processing_status: read('processing_status'), sort: read('sort') || 'relevance',
  language: read('language'), entity_id: readMany('entity_id'), entity_type: readMany('entity_type'), keyword_id: readMany('keyword_id'),
  story_country: read('story_country'), mentioned_country: read('mentioned_country'),
})
const form = reactive(routeForm())
const sourceTerm = ref(''), entityTerm = ref(''), keywordTerm = ref('')
const sourcePages = useInfiniteQuery({ queryKey: ['search-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const entityPages = useInfiniteQuery({ queryKey: ['nlp-entities', entityTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpEntities(entityTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const keywordPages = useInfiniteQuery({ queryKey: ['nlp-keywords', keywordTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpKeywords(keywordTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const sources = computed(() => sourcePages.data.value?.pages.flatMap(page => page.items) ?? [])
const entities = computed(() => entityPages.data.value?.pages.flatMap(page => page.items) ?? [])
const keywords = computed(() => keywordPages.data.value?.pages.flatMap(page => page.items) ?? [])
const criteria = computed(() => ({
  q: read('q'), source_id: readMany('source_id'), source_country: read('country'), after: read('after'), before: read('before'),
  content_available: read('content_available'), processing_status: read('processing_status'), sort: read('sort') || 'relevance',
  language: read('language'), entity_id: readMany('entity_id'), entity_type: readMany('entity_type'), keyword_id: readMany('keyword_id'),
  story_country: read('story_country'), mentioned_country: read('mentioned_country'),
}))
const search = useInfiniteQuery({ queryKey: ['search', criteria], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.search(criteria.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false })
const results = computed(() => search.data.value?.pages.flatMap(page => page.items) ?? [])
const error = computed(() => search.error.value instanceof ApiError ? search.error.value as ApiError : null)
const errorCode = computed(() => error.value?.detail && typeof error.value.detail === 'object' && 'code' in error.value.detail ? String(error.value.detail.code) : '')
const expired = computed(() => error.value?.status === 409 && errorCode.value === 'restart_search')
const upgradeRequired = computed(() => error.value?.status === 409 && errorCode.value === 'search_upgrade_required')
function submit() { router.push({ path: '/search', query: Object.fromEntries(Object.entries(form).filter(([, value]) => Array.isArray(value) ? value.length : value !== '')) }) }
function restart() { search.refetch() }
function openArticle(id: string) { router.push({ path: '/articles', query: { article: id, from: route.fullPath } }) }
watch(() => route.query, () => Object.assign(form, routeForm()))
</script>

<template>
  <header><div><p class="eyebrow">Archive discovery</p><h2>Search</h2></div></header>
  <details class="syntax-help"><summary>Query syntax</summary><p>Use quoted phrases, explicit AND, source:, country:, after:, and before:. Country means source country. Annotation clauses include entity:, keyword:, language:, story_country:, and mentioned_country:. Adjacent terms also use AND.</p></details>
  <form class="search-filters panel" role="search" @submit.prevent="submit">
    <label class="wide">Query<input v-model="form.q" placeholder='climate AND "sea level"' /></label>
    <label>Source search<input v-model="sourceTerm" placeholder="Find active or retired sources" /></label>
    <label>Source<select v-model="form.source_id" multiple><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}{{ source.retired ? ' (retired)' : '' }}</option></select></label>
    <label>Source country<input v-model="form.country" maxlength="2" placeholder="US" /></label>
    <label>Detected language<input v-model="form.language" maxlength="3" placeholder="en" /></label>
    <label>Entity search<input v-model="entityTerm" placeholder="Find an entity" /></label>
    <label>Entity<select v-model="form.entity_id" multiple><option v-for="entity in entities" :key="entity.id" :value="entity.id">{{ entity.text }} ({{ entity.kind }})</option></select></label>
    <label>Entity type<select v-model="form.entity_type" multiple><option v-for="kind in ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER']" :key="kind">{{ kind }}</option></select></label>
    <label>Keyword search<input v-model="keywordTerm" placeholder="Find a keyword" /></label>
    <label>Keyword<select v-model="form.keyword_id" multiple><option v-for="keyword in keywords" :key="keyword.id" :value="keyword.id">{{ keyword.text }}</option></select></label>
    <label>Story country<input v-model="form.story_country" maxlength="2" placeholder="DE" /></label>
    <label>Mentioned country<input v-model="form.mentioned_country" maxlength="2" placeholder="FR" /></label>
    <label>After<input v-model="form.after" type="date" /></label><label>Before<input v-model="form.before" type="date" /></label>
    <label>Content<select v-model="form.content_available"><option value="">Any</option><option value="true">Available</option><option value="false">RSS only</option></select></label>
    <label>Processing<select v-model="form.processing_status"><option value="">Any</option><option v-for="value in ['queued', 'running', 'retrying', 'succeeded', 'failed']" :key="value">{{ value }}</option></select></label>
    <label>Sort<select v-model="form.sort"><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="most_sources">Most sources</option></select></label>
    <button type="submit">Search archive</button>
  </form>
  <section class="panel search-results">
    <p v-if="search.isPending.value" class="muted">Searching archive…</p>
    <template v-else-if="search.isError.value"><p role="alert" class="error">{{ expired ? 'This search snapshot expired. Restart the search.' : upgradeRequired ? 'Search upgrade required. Rebuild the search index to use annotation filters.' : error?.status === 503 ? 'Search is temporarily unavailable.' : error?.message || 'Could not search the archive.' }}</p><button v-if="expired" class="secondary" @click="restart">Restart search</button></template>
    <p v-else-if="!results.length" class="muted">No articles match this search.</p>
    <button v-for="result in results" :key="result.article_id" class="search-result" @click="openArticle(result.article_id)"><strong>{{ result.title }}</strong><span>{{ new Date(result.effective_date).toLocaleString() }} · {{ result.distinct_source_count }} sources</span><small>{{ result.sources.join(', ') }}</small><p v-if="result.highlights.length"><template v-for="(segment, index) in result.highlights" :key="index"><mark v-if="segment.marked">{{ segment.text }}</mark><template v-else>{{ segment.text }}</template></template></p><p v-else-if="result.summary">{{ result.summary }}</p></button>
    <button v-if="search.hasNextPage.value" class="secondary" :disabled="search.isFetchingNextPage.value" @click="search.fetchNextPage()">{{ search.isFetchingNextPage.value ? 'Loading…' : 'Load more' }}</button>
  </section>
</template>
