<script setup lang="ts">
import { computed, defineAsyncComponent, reactive, ref, watch } from 'vue'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { brushRange, INTERVALS, queryFromState, refine, searchParams, stateFromQuery, type Investigation, type ListField } from '../investigation'

const TimelineChart = defineAsyncComponent(() => import('../components/TimelineChart.vue').then(module => module.default))
const route = useRoute(), router = useRouter(), client = useQueryClient()
const state = computed(() => stateFromQuery(route.query))
const joined = (values: string[]) => values.join(', ')
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)
const formFromState = (current: Investigation) => ({
  q: current.q, source_id: [...current.source_id], country: joined(current.source_country), after: current.after ?? '', before: current.before ?? '',
  content_available: current.content_available === null ? '' : String(current.content_available), processing_status: current.processing_status[0] ?? '', sort: current.sort,
  language: joined(current.language), entity_id: [...current.entity_id], entity_type: [...current.entity_type], keyword_id: [...current.keyword_id],
  story_country: joined(current.story_country), mentioned_country: joined(current.mentioned_country),
})
const form = reactive(formFromState(state.value))
const sourceTerm = ref(''), entityTerm = ref(''), keywordTerm = ref(''), saveName = ref('')
const sourcePages = useInfiniteQuery({ queryKey: ['search-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const entityPages = useInfiniteQuery({ queryKey: ['nlp-entities', entityTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpEntities(entityTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const keywordPages = useInfiniteQuery({ queryKey: ['nlp-keywords', keywordTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpKeywords(keywordTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const sources = computed(() => sourcePages.data.value?.pages.flatMap(page => page.items) ?? [])
const entities = computed(() => entityPages.data.value?.pages.flatMap(page => page.items) ?? [])
const keywords = computed(() => keywordPages.data.value?.pages.flatMap(page => page.items) ?? [])
const criteria = computed(() => searchParams(state.value))
const timelineCriteria = computed(() => searchParams(state.value, { interval: true }))
const search = useInfiniteQuery({ queryKey: ['search', criteria], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.search(criteria.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false })
const timeline = useQuery({ queryKey: ['search-timeline', timelineCriteria], queryFn: () => api.timeline(timelineCriteria.value), retry: false })
const results = computed(() => search.data.value?.pages.flatMap(page => page.items) ?? [])
const errorCode = (reason: unknown) => reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' && 'code' in reason.detail ? String(reason.detail.code) : ''
const error = computed(() => search.error.value instanceof ApiError ? search.error.value as ApiError : null)
const expired = computed(() => error.value?.status === 409 && errorCode(error.value) === 'restart_search')
const upgradeRequired = computed(() => error.value?.status === 409 && errorCode(error.value) === 'search_upgrade_required')
const timelineTooFine = computed(() => errorCode(timeline.error.value) === 'timeline_too_fine')
const save = useMutation({ mutationFn: (name: string) => api.createSavedSearch(name, state.value), onSuccess: () => client.invalidateQueries({ queryKey: ['saved-searches'] }) })
function navigate(next: Investigation) { router.push({ path: '/search', query: queryFromState(next) }) }
function submit() {
  navigate({
    ...state.value, q: form.q.trim(), source_id: form.source_id, source_country: split(form.country).map(code => code.toUpperCase()), after: form.after || null, before: form.before || null,
    content_available: form.content_available === '' ? null : form.content_available === 'true', processing_status: form.processing_status ? [form.processing_status] : [],
    sort: form.sort, language: split(form.language), entity_id: form.entity_id, entity_type: form.entity_type, keyword_id: form.keyword_id,
    story_country: split(form.story_country).map(code => code.toUpperCase()), mentioned_country: split(form.mentioned_country).map(code => code.toUpperCase()),
  })
}
function crossFilter(field: ListField, value: string) { navigate(refine(state.value, field, value)) }
function selectRange(range: { start: string; end: string }) {
  // Edge buckets are calendar-aligned and can start before `after` or end after `before`; brushing must only narrow.
  const brushed = brushRange(range.start, range.end), { after, before } = state.value
  navigate({ ...state.value, after: after && after > brushed.after ? after : brushed.after, before: before && before < brushed.before ? before : brushed.before })
}
function setInterval(interval: string) { navigate({ ...state.value, interval: INTERVALS.find(item => item === interval) ?? 'auto' }) }
function saveSearch() { if (saveName.value.trim()) save.mutate(saveName.value.trim()) }
function restart() { search.refetch() }
function openArticle(id: string) { router.push({ path: '/articles', query: { article: id, from: route.fullPath } }) }
const sourceCountries = (refs: { country: string | null }[]) => [...new Set(refs.flatMap(ref => ref.country ? [ref.country] : []))]
watch(state, current => Object.assign(form, formFromState(current)))
</script>

<template>
  <header><div><p class="eyebrow">Archive discovery</p><h2>Search</h2></div></header>
  <details class="syntax-help"><summary>Query syntax</summary><p>Use quoted phrases, explicit AND, source:, country:, after:, and before:. Country means source country. Annotation clauses include entity:, keyword:, language:, story_country:, and mentioned_country:. Adjacent terms also use AND.</p></details>
  <form class="search-filters panel" role="search" @submit.prevent="submit">
    <label class="wide">Query<input v-model="form.q" placeholder='climate AND "sea level"' /></label>
    <label>Source search<input v-model="sourceTerm" placeholder="Find active or retired sources" /></label>
    <label>Source<select v-model="form.source_id" multiple><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}{{ source.retired ? ' (retired)' : '' }}</option></select></label>
    <label>Source country<input v-model="form.country" placeholder="US, GR" /></label>
    <label>Detected language<input v-model="form.language" placeholder="en" /></label>
    <label>Entity search<input v-model="entityTerm" placeholder="Find an entity" /></label>
    <label>Entity<select v-model="form.entity_id" multiple><option v-for="entity in entities" :key="entity.id" :value="entity.id">{{ entity.text }} ({{ entity.kind }})</option></select></label>
    <label>Entity type<select v-model="form.entity_type" multiple><option v-for="kind in ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER']" :key="kind">{{ kind }}</option></select></label>
    <label>Keyword search<input v-model="keywordTerm" placeholder="Find a keyword" /></label>
    <label>Keyword<select v-model="form.keyword_id" multiple><option v-for="keyword in keywords" :key="keyword.id" :value="keyword.id">{{ keyword.text }}</option></select></label>
    <label>Story country<input v-model="form.story_country" placeholder="DE" /></label>
    <label>Mentioned country<input v-model="form.mentioned_country" placeholder="FR" /></label>
    <label>After<input v-model="form.after" type="date" /></label><label>Before<input v-model="form.before" type="date" /></label>
    <label>Content<select v-model="form.content_available"><option value="">Any</option><option value="true">Available</option><option value="false">RSS only</option></select></label>
    <label>Processing<select v-model="form.processing_status"><option value="">Any</option><option v-for="value in ['queued', 'running', 'retrying', 'succeeded', 'failed']" :key="value">{{ value }}</option></select></label>
    <label>Sort<select v-model="form.sort"><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="most_sources">Most sources</option></select></label>
    <button type="submit">Search archive</button>
  </form>
  <section class="panel timeline" aria-labelledby="timeline-heading">
    <div class="timeline-heading"><h3 id="timeline-heading">{{ timeline.data.value?.total ? `${timeline.data.value.total} matching articles over time` : 'Timeline' }}</h3><div class="timeline-controls"><button v-if="state.after || state.before" type="button" class="secondary" @click="navigate({ ...state, after: null, before: null })">Clear date range</button><label>Timeline interval<select :value="state.interval" @change="setInterval(($event.target as HTMLSelectElement).value)"><option v-for="interval in INTERVALS" :key="interval" :value="interval">{{ interval === 'auto' ? `Automatic${timeline.data.value && state.interval === 'auto' ? ` (${timeline.data.value.interval})` : ''}` : interval }}</option></select></label></div></div>
    <p v-if="timeline.isPending.value" class="muted">Charting matches…</p>
    <template v-else-if="timeline.isError.value"><p class="error">{{ timelineTooFine ? `${(timeline.error.value as ApiError).message}. Choose a larger interval.` : 'Could not load the timeline.' }}</p><button v-if="timelineTooFine" type="button" class="secondary" @click="setInterval('auto')">Use automatic interval</button></template>
    <p v-else-if="!timeline.data.value?.buckets.length" class="muted">No matching articles to chart.</p>
    <TimelineChart v-else :buckets="timeline.data.value.buckets" :interval="timeline.data.value.interval" @select="selectRange" />
  </section>
  <form class="panel save-search" @submit.prevent="saveSearch"><label>Saved search name<input v-model="saveName" maxlength="120" placeholder="Energy grid watch" /></label><button type="submit" :disabled="save.isPending.value">Save search</button><p v-if="save.isError.value" role="alert" class="error">{{ save.error.value instanceof ApiError ? save.error.value.message : 'Could not save this search.' }}</p><p v-else-if="save.isSuccess.value" class="success">Saved “{{ save.variables.value }}”.</p></form>
  <section class="panel search-results">
    <p v-if="search.isPending.value" class="muted">Searching archive…</p>
    <template v-else-if="search.isError.value"><p role="alert" class="error">{{ expired ? 'This search snapshot expired. Restart the search.' : upgradeRequired ? 'Search upgrade required. Rebuild the search index to use annotation filters.' : error?.status === 503 ? 'Search is temporarily unavailable.' : error?.message || 'Could not search the archive.' }}</p><button v-if="expired" class="secondary" @click="restart">Restart search</button></template>
    <p v-else-if="!results.length" class="muted">No articles match this search.</p>
    <article v-for="result in results" :key="result.article_id" class="search-result"><button class="result-open" @click="openArticle(result.article_id)"><strong>{{ result.title }}</strong><span>{{ new Date(result.effective_date).toLocaleString() }} · {{ result.distinct_source_count }} sources</span><p v-if="result.highlights.length"><template v-for="(segment, index) in result.highlights" :key="index"><mark v-if="segment.marked">{{ segment.text }}</mark><template v-else>{{ segment.text }}</template></template></p><p v-else-if="result.summary">{{ result.summary }}</p></button><div class="result-filters"><button v-for="source in result.source_refs" :key="source.id" type="button" class="annotation-link" :aria-label="`Filter by source ${source.name}`" @click="crossFilter('source_id', source.id)">{{ source.name }}</button><button v-for="country in sourceCountries(result.source_refs)" :key="`source-${country}`" type="button" class="annotation-link" :aria-label="`Filter by source country ${country}`" @click="crossFilter('source_country', country)">{{ country }}</button><button v-if="result.story_country" type="button" class="annotation-link" :aria-label="`Filter by story country ${result.story_country}`" @click="crossFilter('story_country', result.story_country)">Story: {{ result.story_country }}</button><RouterLink v-if="result.story_cluster && result.story_cluster.source_count > 1" class="annotation-link" :to="{ path: `/clusters/${result.story_cluster.id}`, query: { from: route.fullPath } }">Also reported by {{ result.story_cluster.source_count - 1 }} other source{{ result.story_cluster.source_count - 1 === 1 ? '' : 's' }}</RouterLink></div></article>
    <button v-if="search.hasNextPage.value" class="secondary" :disabled="search.isFetchingNextPage.value" @click="search.fetchNextPage()">{{ search.isFetchingNextPage.value ? 'Loading…' : 'Load more' }}</button>
  </section>
</template>
