<script setup lang="ts">
import { computed } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { queryFromState, refine, stateFromQuery } from '../investigation'

const route = useRoute(), router = useRouter()
// Read defensively rather than cast: once the route navigates away (e.g. opening a member article),
// `route.params.id` becomes undefined for a render or two before this component is torn down.
const id = computed(() => typeof route.params.id === 'string' ? route.params.id : '')
const cluster = useInfiniteQuery({
  queryKey: ['cluster', id], initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }) => api.cluster(id.value, pageParam), getNextPageParam: page => page.members.next_cursor ?? undefined, enabled: () => Boolean(id.value), retry: false,
})
const header = computed(() => cluster.data.value?.pages[0])
const members = computed(() => cluster.data.value?.pages.flatMap(page => page.members.items) ?? [])
const searchWithinStory = computed(() => {
  const from = typeof route.query.from === 'string' && route.query.from.startsWith('/search') ? route.query.from : ''
  const origin = stateFromQuery(from ? router.resolve(from).query : {})
  return { path: '/search', query: queryFromState(id.value ? refine(origin, 'story_cluster_id', id.value) : origin) }
})
function openArticle(articleId: string) { router.push({ path: '/articles', query: { article: articleId, from: route.fullPath } }) }
</script>

<template>
  <header><div><p class="eyebrow">Story</p><h2>Cluster</h2></div></header>
  <section class="panel">
    <p v-if="cluster.isPending.value" class="muted">Loading story…</p>
    <p v-else-if="cluster.isError.value" class="error">Could not load this story.</p>
    <template v-else-if="header">
      <h3>{{ header.article_count }} articles · {{ header.source_count }} sources</h3>
      <p class="muted">
        <template v-if="header.first_published_at && header.last_published_at">{{ new Date(header.first_published_at).toLocaleString() }} – {{ new Date(header.last_published_at).toLocaleString() }}</template>
        <template v-else>Publication dates are not available.</template>
      </p>
      <RouterLink class="annotation-link" :to="searchWithinStory">Search within this story</RouterLink>
      <article v-for="member in members" :key="member.article_id" class="search-result">
        <button class="result-open" @click="openArticle(member.article_id)">
          <strong>{{ member.title }}</strong>
          <span>{{ new Date(member.effective_date).toLocaleString() }} · {{ member.feeds.map(feed => feed.name).join(', ') }}</span>
        </button>
      </article>
      <button v-if="cluster.hasNextPage.value" class="secondary" :disabled="cluster.isFetchingNextPage.value" @click="cluster.fetchNextPage()">{{ cluster.isFetchingNextPage.value ? 'Loading…' : 'Load more' }}</button>
    </template>
  </section>
</template>
