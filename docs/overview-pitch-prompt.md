# Overview analytics pitch — deck-generation prompt

Reusable prompt for pitching the Overview-page analytics features (ingestion
timeline + top entities/countries) to reviewers. Fact-checked against the
codebase on 2026-09-17 — see verification notes at the bottom.

---

<role>
You are a senior product presentation writer who has previously presented
feature pitches to both product leadership and engineering review boards.
You write decks that are confident, specific, and easy to present aloud.
</role>

<context>
NewsIntel is a self-hosted news archive and intelligence platform. It
continuously ingests RSS feeds, extracts full article text, runs NLP to tag
entities, keywords, and countries, clusters related coverage, and provides
full-text search. Backend is FastAPI + PostgreSQL; frontend is React/Next.js
with ECharts.

Today the product's Overview page is a near-empty stub (a single "core
services" status card). Two proposed features would turn it into a real
analytical dashboard. The pitch must convince reviewers these are the right
two features to build now, and clearly explain what each one does and why it
matters.
</context>

<source_material>
Feature 1: Ingestion timeline with spike detection.
- 30-day bar chart of articles ingested per day.
- Spike detector flags days at or above 2x the trailing 7-day average.
- Reads PostgreSQL `Article.first_discovered_at` timestamps, so it keeps
  working even when Elasticsearch/search is down (an explicit product rule).
- Click a day or a flagged spike to open a date-filtered search of that day's
  articles; reuses the product's existing URL-driven investigation state.

Feature 2: Top entities and top countries panel.
- Two side-by-side horizontal bar charts over a rolling window.
- Top 10 named entities, filterable by type such as PERSON or ORG.
- Top primary-story countries by article volume.
- Click any bar to open a pre-filtered search: all articles mentioning that
  entity, or all articles whose primary story country is that country.

Why these two, why now:
- Stat counters, feed health, and failed jobs already exist on the Jobs page,
  so these two add genuinely new analytical value instead of duplicating it.
- They reuse proven building blocks: the existing ECharts timeline component
  and the investigation-state URL helpers, so implementation risk is low.
- They directly satisfy the product spec's overview requirements: ingestion
  rate, notable recent spikes, and top entities/countries.
- Planned implementation: three new PostgreSQL aggregation endpoints under a
  new `analytics` backend domain, two chart panels, and a pure unit-tested
  spike-detection helper.

Use ONLY facts from this source material in the deck. Do not invent metrics,
numbers, timelines, or product claims that are not listed here.
</source_material>

<task>
Create a presentation to pitch these two features, as a complete slide-by-slide
outline. Aim for 10-12 slides. Tell the story in this order:
1. Why now: the Overview page is currently a stub and the two most important
   analytical answers are missing.
2. Feature 1 in detail, then Feature 2 in detail, including how each
   feature's interactivity (clicks and drill-downs) works.
3. How the two features work together as a daily analyst workflow.
4. Why these two and not the usual dashboard counters.
5. Technical fit and low implementation risk.
6. What this milestone sets up next.
7. Concrete next steps.

Suggested slide allocation (guidance, not a hard rule — total must stay
10-12): 1 title, 1 why-now, 2 for Feature 1 (what it shows, then its
interactivity), 2 for Feature 2 (what it shows, then its interactivity), 1
combined workflow, 1 why-these-two, 1 technical fit, 1 what's next, 1
concrete next steps. That's 11 — add or drop one slide only within the
why-now or workflow beats, since those are the ones with room to expand or
compress without losing a required story beat.
</task>

<instructions>
1. For every slide, provide exactly three parts in this order: a one-line
   headline; 3-5 specific bullet points; and a 1-2 sentence speaker note that
   says something a human did not just read off the slide.
2. Bullets are separate sentences inside the slide's paragraph, not literal
   bullet characters (no "-", "*", or "•"). This matches the format rule
   below (plain text, one paragraph per slide) — see the example.
3. Keep claims grounded in <source_material>. If an amount or number is not in
   the source material, do not invent one.
4. Keep the tone confident and natural. No emojis, no exclamations, no
   marketing fluff. Avoid jargon unless it is explained or obvious to both
   product and engineering audiences.
5. Format the deck as plain text, one paragraph per slide, prefixed with
   "Slide N:" exactly as the examples show. Do not use markdown headings,
   bold, or bullet symbols.
</instructions>

<examples>
Example of the required format:

Slide 1: From Stub to Signal: Turning the Overview Page Into a Real
Intelligence Dashboard. Two features that answer: what is happening now, and
what are we covering. Speaker note: The Overview is the first screen an
analyst opens, and today it tells them almost nothing.

Slide 2: The Overview page today is one "core services" status card.
Operational data exists but is scattered across Jobs, Sources, and Search.
Nobody can answer the two most urgent questions at a glance. The product spec
requires the overview to include ingestion rate, spikes, and top entities and
countries. Speaker note: We are not adding noise; we are adding the two most
analytically valuable answers the overview is missing.

Slide 3: A 30-day bar chart of articles ingested per day. An automatic spike
detector flags days at or above two times the trailing 7-day average.
Highlighted days instantly read as "something big is happening." Built on
PostgreSQL ingestion timestamps, so it stays online even when search is down.
Speaker note: The chart reads from the canonical database, not Elasticsearch,
so the overview keeps working during a search outage.
</examples>

<acceptance_criteria>
Before you finish, verify your draft against all of the following and fix any
shortcomings before responding:
- The deck has 10-12 slides.
- Both features are described accurately using only <source_material> facts.
- Every slide has a headline, 3-5 bullets, and a speaker note.
- The "why now" and "why these two" arguments are present and specific.
- The story order requested in <task> is followed.
- No emojis, no invented numbers or claims, and no facts outside
  <source_material>.
- The plain text format matches the examples exactly (no markdown markup,
  no literal bullet characters — bullets are sentences within the paragraph).
</acceptance_criteria>

Produce the 10-12 slide presentation now, in plain text, following the
examples and acceptance criteria.

---

## Verification notes (2026-09-17)

Every claim in `<source_material>` was checked against the codebase and confirmed:

- Overview page is a stub: `frontend/src/app/page.tsx` renders only a header and one core-services status card.
- Reusable ECharts timeline component: `frontend/src/components/TimelineChart.tsx`, already used in `frontend/src/app/search/page.tsx`.
- Investigation-state URL helpers: `frontend/src/lib/investigation.ts` (`stateFromQuery`, `queryFromState`, `refine`, etc.).
- `Article.first_discovered_at`: `backend/app/feeds/models.py`, indexed `DateTime(timezone=True)` column.
- No existing `analytics` backend domain — confirmed genuinely new (`backend/app/` has `core, auth, api, db, jobs, feeds, articles, search, nlp, investigations, clustering, graph`, no `analytics`).
- Jobs page already shows stat counters and failed-job handling: `frontend/src/app/jobs/page.tsx`.
- Per-article entity/keyword/country data exists and is aggregation-ready: `backend/app/nlp/models.py` (`Entity`/`ArticleEntity`, `Keyword`/`ArticleKeyword`, `ArticleCountryAnnotation` with a `role` column distinguishing primary/story country).

Two fixes applied to the original draft prompt (both editorial, not factual — no source-material fact was wrong):
1. Clarified that "3-5 bullet points" means separate sentences within the one-paragraph-per-slide format, not literal bullet characters — the original instructions and the "no bullet symbols" format rule were in tension.
2. Added a suggested per-beat slide allocation so the 7 required story beats reliably fit inside 10-12 slides without one beat crowding out another.
