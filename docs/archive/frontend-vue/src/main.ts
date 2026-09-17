import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
import './style.css'

createApp(App).use(createPinia()).use(VueQueryPlugin).use(router).mount('#app')
