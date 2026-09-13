<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
const route = useRoute(), router = useRouter()
const feedId = computed(() => typeof route.query.feed === 'string' ? route.query.feed : undefined)
const selectedId = computed(() => typeof route.query.article === 'string' ? route.query.article : undefined)
const feeds = useQuery({ queryKey: ['feeds'], queryFn: () => api.feeds() })
const articles = useQuery({ queryKey: ['articles', feedId], queryFn: () => api.articles(feedId.value) })
const detail = useQuery({ queryKey: ['article', selectedId], queryFn: () => api.article(selectedId.value!), enabled: () => Boolean(selectedId.value) })
function setQuery(values: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...values } }) }
</script>
<template><header><div><p class="eyebrow">RSS archive</p><h2>Articles</h2></div><label class="filter">Source<select :value="feedId" @change="setQuery({ feed: ($event.target as HTMLSelectElement).value || undefined, article: undefined })"><option value="">All sources</option><option v-for="feed in feeds.data.value?.items" :key="feed.id" :value="feed.id">{{ feed.name }}</option></select></label></header>
  <div class="article-layout"><section class="article-list panel"><p v-if="!articles.data.value?.items.length" class="muted">No collected articles.</p><button v-for="article in articles.data.value?.items" :key="article.id" class="article-row" @click="setQuery({ article: article.id })"><strong>{{ article.title }}</strong><span>{{ new Date(article.first_discovered_at).toLocaleString() }}</span><small>{{ article.provenance.map(p => p.feed_name).join(', ') }}</small></button></section>
  <section class="panel detail"><p v-if="!selectedId" class="muted">Select an article to inspect its feed record.</p><template v-else-if="detail.data.value"><p class="eyebrow">Article detail</p><h3>{{ detail.data.value.title }}</h3><a :href="detail.data.value.original_url" target="_blank" rel="noopener noreferrer">Open original article</a><dl><dt>Published</dt><dd>{{ detail.data.value.published_at ? new Date(detail.data.value.published_at).toLocaleString() : 'Not supplied' }}</dd><dt>First discovered</dt><dd>{{ new Date(detail.data.value.first_discovered_at).toLocaleString() }}</dd></dl><article v-for="source in detail.data.value.provenance" :key="source.feed_id" class="provenance"><strong>{{ source.feed_name }}</strong><p>{{ source.description || 'No RSS description.' }}</p></article></template></section></div>
</template>
