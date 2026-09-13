import { createRouter, createWebHistory } from 'vue-router'
import ArticlesView from './views/ArticlesView.vue'
import OverviewView from './views/OverviewView.vue'
import SourcesView from './views/SourcesView.vue'

export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', component: OverviewView }, { path: '/sources', component: SourcesView }, { path: '/articles', component: ArticlesView },
] })
