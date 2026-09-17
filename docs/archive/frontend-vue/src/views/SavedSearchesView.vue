<script setup lang="ts">
import { computed, ref } from 'vue'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { SavedSearch } from '../api-types'
import { fromSaved, queryFromState } from '../investigation'

const router = useRouter(), client = useQueryClient()
const renamingId = ref<string | null>(null), newName = ref('')
const pages = useInfiniteQuery({ queryKey: ['saved-searches'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.savedSearches(pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
const items = computed(() => pages.data.value?.pages.flatMap(page => page.items) ?? [])
const refresh = () => client.invalidateQueries({ queryKey: ['saved-searches'] })
const rename = useMutation({ mutationFn: ({ id, name }: { id: string; name: string }) => api.updateSavedSearch(id, { name }), onSuccess: () => { renamingId.value = null; refresh() } })
const remove = useMutation({ mutationFn: (id: string) => api.deleteSavedSearch(id), onSuccess: refresh })
const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
function openHref(item: SavedSearch) { return item.state ? router.resolve({ path: '/search', query: queryFromState(fromSaved(item.state)) }).fullPath : '' }
function startRename(item: SavedSearch) { rename.reset(); renamingId.value = item.id; newName.value = item.name }
function submitRename(item: SavedSearch) { if (newName.value.trim()) rename.mutate({ id: item.id, name: newName.value.trim() }) }
function confirmDelete(item: SavedSearch) { if (window.confirm(`Delete the saved search “${item.name}”?`)) remove.mutate(item.id) }
</script>

<template>
  <header><div><p class="eyebrow">Investigations</p><h2>Saved Searches</h2></div><RouterLink class="secondary" to="/search">New search</RouterLink></header>
  <section class="panel saved-searches">
    <p v-if="pages.isPending.value" class="muted">Loading saved searches…</p>
    <p v-else-if="pages.isError.value" class="error">Could not load saved searches.</p>
    <p v-else-if="!items.length" class="muted">No saved searches yet. Save one from Search to reopen the full investigation later.</p>
    <p v-if="remove.isError.value" role="alert" class="error">{{ message(remove.error.value, 'Could not delete this saved search.') }}</p>
    <article v-for="item in items" :key="item.id" class="saved-search-row">
      <div>
        <form v-if="renamingId === item.id" class="rename-form" @submit.prevent="submitRename(item)"><label>New name for {{ item.name }}<input v-model="newName" maxlength="120" /></label><div class="actions"><button type="submit" :disabled="rename.isPending.value">Save name</button><button type="button" class="secondary" @click="renamingId = null">Cancel</button></div><p v-if="rename.isError.value" role="alert" class="error">{{ message(rename.error.value, 'Could not rename this saved search.') }}</p></form>
        <template v-else><strong>{{ item.name }}</strong><small>Updated {{ new Date(item.updated_at).toLocaleString() }}<template v-if="item.state?.q"> · {{ item.state.q }}</template></small></template>
        <p v-if="!item.state" class="error">This saved search can no longer be opened: {{ item.problem || 'its stored state is not supported.' }}</p>
      </div>
      <div class="actions"><RouterLink v-if="item.state" class="annotation-link" :aria-label="`Open ${item.name}`" :to="openHref(item)">Open</RouterLink><button v-if="renamingId !== item.id" type="button" class="secondary" :aria-label="`Rename ${item.name}`" @click="startRename(item)">Rename</button><button type="button" class="danger" :aria-label="`Delete ${item.name}`" :disabled="remove.isPending.value" @click="confirmDelete(item)">Delete</button></div>
    </article>
    <button v-if="pages.hasNextPage.value" class="secondary" :disabled="pages.isFetchingNextPage.value" @click="pages.fetchNextPage()">{{ pages.isFetchingNextPage.value ? 'Loading…' : 'Load more saved searches' }}</button>
  </section>
</template>
