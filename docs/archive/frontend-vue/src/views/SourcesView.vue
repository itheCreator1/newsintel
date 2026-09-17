<script setup lang="ts">
import { ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { api } from '../api'
import type { Feed } from '../api-types'
const client = useQueryClient(), cursor = ref<string>(), selected = ref<Feed>()
const name = ref(''), url = ref(''), country = ref(''), language = ref(''), interval = ref(30), mode = ref<'rss' | 'full_text' | 'full_text_html'>('rss')
const feeds = useQuery({ queryKey: ['feeds', cursor], queryFn: () => api.feeds(cursor.value) })
const save = useMutation({ mutationFn: () => api.createFeed({ name: name.value, url: url.value, source_country: country.value || undefined, expected_language: language.value || undefined, poll_interval_minutes: interval.value, fetching_mode: mode.value }), onSuccess: () => { name.value = ''; url.value = ''; client.invalidateQueries({ queryKey: ['feeds'] }) } })
const update = useMutation({ mutationFn: ({ feed, changes }: { feed: Feed; changes: Partial<Feed> }) => api.updateFeed(feed.id, changes), onSuccess: () => client.invalidateQueries({ queryKey: ['feeds'] }) })
const poll = useMutation({ mutationFn: api.pollFeed, onSuccess: () => client.invalidateQueries({ queryKey: ['feeds'] }) })
const retire = useMutation({ mutationFn: api.retireFeed, onSuccess: () => { selected.value = undefined; client.invalidateQueries({ queryKey: ['feeds'] }) } })
const history = useQuery({ queryKey: ['fetches', selected], queryFn: () => api.fetches(selected.value!.id), enabled: () => Boolean(selected.value) })
function confirmRetire(feed: Feed) { if (window.confirm('Retire this source? Its archive will be preserved.')) retire.mutate(feed.id) }
</script>
<template>
  <header><div><p class="eyebrow">Collection</p><h2>Sources</h2></div></header>
  <p class="lede">Choose RSS metadata, readable full text, or full text with retained source HTML for each source.</p>
  <div class="two-column"><section class="panel"><h3>Add source</h3><form @submit.prevent="save.mutate()">
    <label>Name<input v-model="name" required maxlength="200" /></label><label>Feed URL<input v-model="url" required type="url" /></label>
    <div class="form-row"><label>Source country<input v-model="country" maxlength="2" placeholder="GR" /></label><label>Expected language<input v-model="language" maxlength="16" placeholder="el" /></label></div>
    <label>Collection mode<select v-model="mode"><option value="rss">RSS metadata only</option><option value="full_text">Readable full text</option><option value="full_text_html">Full text + retained HTML</option></select></label>
    <label>Poll interval (minutes)<input v-model.number="interval" type="number" min="5" /></label><button>Add source</button>
  </form></section><section class="panel"><h3>Managed sources</h3><p v-if="feeds.isPending.value">Loading…</p><p v-else-if="!feeds.data.value?.items.length" class="muted">No sources yet.</p>
    <article v-for="feed in feeds.data.value?.items" :key="feed.id" class="source-row" @click="selected = feed"><div><strong>{{ feed.name }}</strong><small>{{ feed.url }}</small><small>{{ feed.fetching_mode.split('_').join(' ') }}</small></div><span :class="['badge', feed.last_success_at ? 'healthy' : 'pending']">{{ feed.enabled ? (feed.last_success_at ? 'Healthy' : 'Awaiting poll') : 'Disabled' }}</span><div class="actions"><select :value="feed.fetching_mode" aria-label="Collection mode" @click.stop @change="update.mutate({ feed, changes: { fetching_mode: ($event.target as HTMLSelectElement).value } })"><option value="rss">RSS only</option><option value="full_text">Full text</option><option value="full_text_html">Full text + HTML</option></select><button class="secondary" @click.stop="update.mutate({ feed, changes: { enabled: !feed.enabled } })">{{ feed.enabled ? 'Disable' : 'Enable' }}</button><button class="secondary" @click.stop="poll.mutate(feed.id)">Poll now</button><button class="danger" @click.stop="confirmRetire(feed)">Retire</button></div></article>
    <button v-if="feeds.data.value?.next_cursor" class="secondary" @click="cursor = feeds.data.value?.next_cursor || undefined">Next page</button>
  </section></div>
  <section v-if="selected" class="panel history"><h3>{{ selected.name }} fetch history</h3><p v-if="!history.data.value?.items.length" class="muted">No fetch attempts.</p><article v-for="fetch in history.data.value?.items" :key="fetch.id" class="history-row"><strong>{{ fetch.status }}</strong><span>{{ new Date(fetch.started_at).toLocaleString() }}</span><span>{{ fetch.new_article_count }} new / {{ fetch.entry_count }} entries</span><span v-if="fetch.error_message" class="error">{{ fetch.error_category }}: {{ fetch.error_message }}</span></article></section>
</template>
