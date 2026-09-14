import { createRouter, createWebHistory } from 'vue-router'
import ArticlesView from './views/ArticlesView.vue'
import OverviewView from './views/OverviewView.vue'
import SourcesView from './views/SourcesView.vue'
import JobsView from './views/JobsView.vue'
import SearchView from './views/SearchView.vue'

export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', component: OverviewView }, { path: '/sources', component: SourcesView }, { path: '/articles', component: ArticlesView }, { path: '/search', component: SearchView }, { path: '/jobs', component: JobsView },
] })
