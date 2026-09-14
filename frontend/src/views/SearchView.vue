<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'

const route = useRoute(), router = useRouter()
const read = (key: string) => typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const form = reactive({ q: read('q'), source: read('source_id'), country: read('country'), after: read('after'), before: read('before'), content: read('content_available'), processing: read('processing_status'), sort: read('sort') || 'relevance' })
const sourceTerm = ref('')
const sourcePages = useInfiniteQuery({ queryKey: ['search-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const sources = computed(() => sourcePages.data.value?.pages.flatMap(page => page.items) ?? [])
const criteria = computed(() => ({ q: read('q'), source_id: read('source_id'), source_country: read('country'), after: read('after'), before: read('before'), content_available: read('content_available'), processing_status: read('processing_status'), sort: read('sort') || 'relevance' }))
const search = useInfiniteQuery({ queryKey: ['search', criteria], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.search(criteria.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false })
const results = computed(() => search.data.value?.pages.flatMap(page => page.items) ?? [])
const error = computed(() => search.error.value instanceof ApiError ? search.error.value as ApiError : null)
const expired = computed(() => error.value?.status === 409)
function submit() { router.push({ path: '/search', query: Object.fromEntries(Object.entries(form).filter(([, value]) => value !== '')) }) }
function restart() { search.refetch() }
function openArticle(id: string) { router.push({ path: '/articles', query: { article: id, from: route.fullPath } }) }
watch(() => route.query, () => Object.assign(form, { q: read('q'), source: read('source_id'), country: read('country'), after: read('after'), before: read('before'), content: read('content_available'), processing: read('processing_status'), sort: read('sort') || 'relevance' }))
</script>

<template><header><div><p class="eyebrow">Archive discovery</p><h2>Search</h2></div></header>
  <details class="syntax-help"><summary>Query syntax</summary><p>Use quoted phrases, explicit AND, source:, country:, after:, and before:. Country means source country. Adjacent terms also use AND.</p></details>
  <form class="search-filters panel" role="search" @submit.prevent="submit"><label class="wide">Query<input v-model="form.q" placeholder='climate AND "sea level"' /></label><label>Source search<input v-model="sourceTerm" placeholder="Find active or retired sources" /></label><label>Source<select v-model="form.source"><option value="">All sources</option><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}{{ source.retired ? ' (retired)' : '' }}</option></select></label><label>Source country<input v-model="form.country" maxlength="2" placeholder="US" /></label><label>After<input v-model="form.after" type="date" /></label><label>Before<input v-model="form.before" type="date" /></label><label>Content<select v-model="form.content"><option value="">Any</option><option value="true">Available</option><option value="false">RSS only</option></select></label><label>Processing<select v-model="form.processing"><option value="">Any</option><option v-for="value in ['queued', 'running', 'retrying', 'succeeded', 'failed']" :key="value">{{ value }}</option></select></label><label>Sort<select v-model="form.sort"><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="most_sources">Most sources</option></select></label><button type="submit">Search archive</button></form>
  <section class="panel search-results"><p v-if="search.isPending.value" class="muted">Searching archive…</p><template v-else-if="search.isError.value"><p role="alert" class="error">{{ expired ? 'This search snapshot expired. Restart the search.' : error?.status === 503 ? 'Search is temporarily unavailable.' : error?.message || 'Could not search the archive.' }}</p><button v-if="expired" class="secondary" @click="restart">Restart search</button></template><p v-else-if="!results.length" class="muted">No articles match this search.</p><button v-for="result in results" :key="result.article_id" class="search-result" @click="openArticle(result.article_id)"><strong>{{ result.title }}</strong><span>{{ new Date(result.effective_date).toLocaleString() }} · {{ result.distinct_source_count }} sources</span><small>{{ result.sources.join(', ') }}</small><p v-if="result.highlights.length"><template v-for="(segment, index) in result.highlights" :key="index"><mark v-if="segment.marked">{{ segment.text }}</mark><template v-else>{{ segment.text }}</template></template></p><p v-else-if="result.summary">{{ result.summary }}</p></button><button v-if="search.hasNextPage.value" class="secondary" :disabled="search.isFetchingNextPage.value" @click="search.fetchNextPage()">{{ search.isFetchingNextPage.value ? 'Loading…' : 'Load more' }}</button></section>
</template>
