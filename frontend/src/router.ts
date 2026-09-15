import { createRouter, createWebHistory } from 'vue-router'
import ArticlesView from './views/ArticlesView.vue'
import ClusterView from './views/ClusterView.vue'
import GraphView from './views/GraphView.vue'
import OverviewView from './views/OverviewView.vue'
import SourcesView from './views/SourcesView.vue'
import JobsView from './views/JobsView.vue'
import SavedSearchesView from './views/SavedSearchesView.vue'
import SearchView from './views/SearchView.vue'
import SettingsView from './views/SettingsView.vue'

export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', component: OverviewView }, { path: '/sources', component: SourcesView }, { path: '/articles', component: ArticlesView }, { path: '/search', component: SearchView }, { path: '/clusters/:id', component: ClusterView }, { path: '/graph', component: GraphView }, { path: '/saved-searches', component: SavedSearchesView }, { path: '/jobs', component: JobsView }, { path: '/settings', component: SettingsView },
] })
