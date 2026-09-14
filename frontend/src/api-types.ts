import type { components } from './types.generated'

export type Feed = components['schemas']['FeedResponse']
export type FeedFetch = components['schemas']['FetchResponse']
export type Article = components['schemas']['ArticleResponse']
export type ArticleDetail = components['schemas']['ArticleDetailResponse']
export type ArticleProvenance = components['schemas']['ArticleProvenance']
export type ProcessingJob = components['schemas']['JobResponse']
export type Backlog = components['schemas']['BacklogResponse']
export type SearchPage = components['schemas']['SearchPage']
export type SearchSourcePage = components['schemas']['SearchSourcePage']
export type IndexStatus = components['schemas']['IndexStatus']
export type IndexFailurePage = components['schemas']['IndexFailurePage']
export interface CursorPage<T> { items: T[]; next_cursor: string | null }
