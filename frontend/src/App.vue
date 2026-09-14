<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { api, type User } from './api'

const user = ref<User | null>(null)
const username = ref('')
const password = ref('')
const error = ref('')

onMounted(async () => {
  try { user.value = await api.me() }
  catch { user.value = null }
})

async function signIn() {
  error.value = ''
  try { user.value = await api.login(username.value, password.value) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : 'Sign in failed' }
}

async function signOut() {
  await api.logout()
  user.value = null
  password.value = ''
}
</script>

<template>
  <main v-if="!user" class="login-page">
    <section class="login-card">
      <p class="eyebrow">Self-hosted news intelligence</p>
      <h1>NewsIntel</h1>
      <p class="lede">Your archive, investigations, and operational picture in one place.</p>
      <form @submit.prevent="signIn">
        <label>Username<input v-model="username" autocomplete="username" required /></label>
        <label>Password<input v-model="password" type="password" autocomplete="current-password" required minlength="12" /></label>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <button type="submit">Sign in</button>
      </form>
    </section>
  </main>
  <div v-else class="shell">
    <aside><h1>NewsIntel</h1><nav aria-label="Main navigation"><RouterLink to="/">Overview</RouterLink><RouterLink to="/sources">Sources</RouterLink><RouterLink to="/articles">Articles</RouterLink><RouterLink to="/search">Search</RouterLink><RouterLink to="/jobs">Jobs</RouterLink></nav><button class="secondary signout" @click="signOut">Sign out</button></aside>
    <main class="dashboard">
      <RouterView />
    </main>
  </div>
</template>
