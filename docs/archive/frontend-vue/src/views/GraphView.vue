<script setup lang="ts">
import { computed, defineAsyncComponent, reactive, ref, watch } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { queryFromState, refine, stateFromQuery, type Investigation } from '../investigation'

const EntityGraph = defineAsyncComponent(() => import('../components/EntityGraph.vue').then(module => module.default))
const route = useRoute(), router = useRouter()
const state = computed(() => stateFromQuery(route.query))
const focus = computed(() => typeof route.query.focus === 'string' ? route.query.focus : '')
const MAX_NODES = 50
const nodeCount = computed(() => {
  const raw = typeof route.query.nodes === 'string' ? Number(route.query.nodes) : NaN
  // A hand-edited URL or bookmark can carry any value; the backend rejects anything over MAX_NODES with a 422.
  return Number.isFinite(raw) && raw > 0 ? Math.min(Math.floor(raw), MAX_NODES) : 30
})
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)
const formFromState = (current: Investigation) => ({
  q: current.q, source_id: [...current.source_id], country: current.source_country.join(', '), story_country: current.story_country.join(', '),
  entity_type: [...current.entity_type], after: current.after ?? '', before: current.before ?? '', nodes: String(nodeCount.value),
})
const form = reactive(formFromState(state.value))
const sourceTerm = ref('')
const sourcePages = useInfiniteQuery({ queryKey: ['graph-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm.value, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const sources = computed(() => sourcePages.data.value?.pages.flatMap(page => page.items) ?? [])

// Only the fields the graph filter form exposes are sent: the graph endpoint accepts more (entity_id,
// keyword_id, story_cluster_id, ...) via the shared search criteria, but leaking whatever happens to be
// in the URL from another view would silently change results the user never asked to filter by.
const graphFilters = computed(() => {
  const filters: Record<string, string | string[] | undefined> = {}
  if (state.value.q) filters.q = state.value.q
  if (state.value.source_id.length) filters.source_id = state.value.source_id
  if (state.value.source_country.length) filters.source_country = state.value.source_country
  if (state.value.story_country.length) filters.story_country = state.value.story_country
  if (state.value.entity_type.length) filters.entity_type = state.value.entity_type
  if (state.value.after) filters.after = state.value.after
  if (state.value.before) filters.before = state.value.before
  filters.nodes = String(nodeCount.value)
  if (focus.value) filters.focus_entity_id = focus.value
  return filters
})
const graph = useQuery({ queryKey: ['entity-graph', graphFilters], queryFn: () => api.entityGraph(graphFilters.value), retry: false })
const nodes = computed(() => graph.data.value?.nodes ?? [])
const edges = computed(() => graph.data.value?.edges ?? [])
const focusNode = computed(() => nodes.value.find(node => node.id === focus.value) ?? null)
const connected = computed(() => {
  const current = focusNode.value
  if (!current) return []
  const ids = new Set(edges.value.filter(edge => edge.source === current.id || edge.target === current.id).map(edge => edge.source === current.id ? edge.target : edge.source))
  return nodes.value.filter(node => ids.has(node.id))
})
const articleCriteria = computed(() => {
  const current = focusNode.value
  if (!current) return null
  return {
    q: state.value.q || undefined, source_country: state.value.source_country.length ? state.value.source_country : undefined,
    story_country: state.value.story_country.length ? state.value.story_country : undefined, after: state.value.after ?? undefined,
    before: state.value.before ?? undefined, entity_id: [current.id],
  }
})
const articles = useQuery({ queryKey: ['entity-graph-articles', articleCriteria], queryFn: () => api.search(articleCriteria.value!), enabled: () => Boolean(articleCriteria.value), retry: false })
const errorCode = (reason: unknown) => reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' && 'code' in reason.detail ? String(reason.detail.code) : ''
const error = computed(() => graph.error.value instanceof ApiError ? graph.error.value as ApiError : null)
const upgradeRequired = computed(() => error.value?.status === 409 && errorCode(error.value) === 'search_upgrade_required')

function navigate(next: Investigation, extra: { focus?: string; nodes?: number } = {}) {
  const query = queryFromState(next)
  const nextFocus = extra.focus !== undefined ? extra.focus : focus.value
  const nextNodes = Math.min(extra.nodes !== undefined ? extra.nodes : nodeCount.value, MAX_NODES)
  if (nextFocus) query.focus = nextFocus
  if (nextNodes !== 30) query.nodes = String(nextNodes)
  router.push({ path: '/graph', query })
}
function submit() {
  navigate({
    ...state.value, q: form.q.trim(), source_id: form.source_id, source_country: split(form.country).map(code => code.toUpperCase()),
    story_country: split(form.story_country).map(code => code.toUpperCase()), entity_type: form.entity_type,
    after: form.after || null, before: form.before || null,
  }, { nodes: Number(form.nodes) || 30 })
}
function selectEntity(entityId: string) { navigate(state.value, { focus: entityId }) }
function searchWithEntity(entityId: string) { return { path: '/search', query: queryFromState(refine(state.value, 'entity_id', entityId)) } }
watch(state, current => Object.assign(form, formFromState(current)))
</script>

<template>
  <header><div><p class="eyebrow">Relationships</p><h2>Graph</h2></div></header>
  <form class="search-filters panel" role="search" @submit.prevent="submit">
    <label class="wide">Query<input v-model="form.q" placeholder='climate AND "sea level"' /></label>
    <label>Source search<input v-model="sourceTerm" placeholder="Find active or retired sources" /></label>
    <label>Source<select v-model="form.source_id" multiple><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}{{ source.retired ? ' (retired)' : '' }}</option></select></label>
    <label>Source country<input v-model="form.country" placeholder="US, GR" /></label>
    <label>Story country<input v-model="form.story_country" placeholder="DE" /></label>
    <label>Entity type<select v-model="form.entity_type" multiple><option v-for="kind in ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER']" :key="kind">{{ kind }}</option></select></label>
    <label>After<input v-model="form.after" type="date" /></label><label>Before<input v-model="form.before" type="date" /></label>
    <label>Nodes<input v-model="form.nodes" type="number" min="1" max="50" /></label>
    <button type="submit">Update graph</button>
  </form>
  <div class="two-column">
    <section class="panel graph-panel">
      <p v-if="graph.isPending.value" class="muted">Loading the entity graph…</p>
      <p v-else-if="graph.isError.value" role="alert" class="error">{{ upgradeRequired ? 'Search upgrade required. Rebuild the search index to use the entity graph.' : error?.status === 503 ? 'The entity graph is temporarily unavailable.' : 'Could not load the entity graph.' }}</p>
      <p v-else-if="!nodes.length" class="muted">No co-occurring entities for these filters.</p>
      <template v-else>
        <EntityGraph :nodes="nodes" :edges="edges" :focus="focus" @select="selectEntity" />
        <p v-if="graph.data.value?.truncated" class="muted">Showing a bounded subset of the graph. Narrow the filters to see more.</p>
        <ul class="graph-node-list" aria-label="Entities in this graph">
          <li v-for="node in nodes" :key="node.id"><button type="button" class="annotation-link" :aria-pressed="node.id === focus" @click="selectEntity(node.id)">{{ node.text }} ({{ node.type }}) · {{ node.article_count }}</button></li>
        </ul>
      </template>
    </section>
    <aside v-if="focusNode" class="panel" aria-label="Entity details">
      <p class="eyebrow">{{ focusNode.type }}</p>
      <h3>{{ focusNode.text }}</h3>
      <p class="muted">{{ focusNode.article_count }} articles</p>
      <RouterLink class="annotation-link" :to="searchWithEntity(focusNode.id)">Search articles with {{ focusNode.text }}</RouterLink>
      <div class="annotation-group">
        <strong>Connected entities</strong>
        <button v-for="node in connected" :key="node.id" type="button" class="annotation-link" @click="selectEntity(node.id)">{{ node.text }}</button>
        <template v-if="!connected.length">None</template>
      </div>
      <div class="annotation-group">
        <strong>Top articles</strong>
        <p v-if="articles.isPending.value" class="muted">Loading articles…</p>
        <p v-else-if="articles.isError.value" class="error">Could not load articles.</p>
        <p v-else-if="!articles.data.value?.items.length" class="muted">No matching articles.</p>
        <RouterLink v-for="result in articles.data.value?.items ?? []" :key="result.article_id" class="annotation-link" :to="{ path: '/articles', query: { article: result.article_id, from: route.fullPath } }">{{ result.title }}</RouterLink>
      </div>
    </aside>
  </div>
</template>
