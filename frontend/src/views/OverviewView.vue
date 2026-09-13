<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { api } from '../api'
const status = useQuery({ queryKey: ['status'], queryFn: api.status })
</script>
<template><header><div><p class="eyebrow">System overview</p><h2>Archive operations</h2></div></header><section class="status-card"><span class="status-dot" /><div><strong>Core services</strong><p>{{ status.data.value?.status || 'Checking' }}</p></div></section></template>
