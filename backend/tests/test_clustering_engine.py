import uuid
from datetime import UTC, datetime, timedelta

from app.clustering.engine import (
    CLUSTER_MINIMUM_SHARED_TERMS,
    CLUSTER_SCORE_THRESHOLD,
    CLUSTER_TIME_WINDOW,
    TERM_OVERLAP_CEILING,
    TERM_OVERLAP_FLOOR,
    TERM_WEIGHT_MINIMUM_WINDOW,
    ArticleFeatures,
    ClusterSummary,
    RuleClusterer,
    StoryClusterer,
    cluster_statistics,
    jaccard,
    lede_terms,
    rank_candidates,
    score_articles,
    select_survivor,
    shares_source,
    stem,
    term_overlap,
    term_weights,
    time_proximity,
    title_tokens,
)

STOP_WORDS = frozenset({"a", "after", "and", "for", "in", "of", "the"})
BASE_TIME = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def article(
    *,
    title: str,
    hours: float = 0,
    entities: frozenset[uuid.UUID] = frozenset(),
    feeds: frozenset[uuid.UUID] = frozenset(),
    article_id: uuid.UUID | None = None,
    title_hash: str = "hash-a",
    terms: frozenset[str] = frozenset(),
) -> ArticleFeatures:
    return ArticleFeatures(
        article_id=article_id or uuid.uuid4(),
        title=title,
        normalized_title_hash=title_hash,
        effective_date=BASE_TIME + timedelta(hours=hours),
        entity_ids=entities,
        feed_ids=feeds,
        terms=terms,
    )


def test_title_tokens_casefold_and_drop_stop_words_and_punctuation() -> None:
    tokens = title_tokens("The Harbor Council, and the Dredging Report!", STOP_WORDS)
    assert tokens == frozenset({"harbor", "council", "dredging", "report"})


def test_title_tokens_split_on_punctuation_and_keep_alphanumeric_terms() -> None:
    assert title_tokens("Budget 2026 vote — B12", STOP_WORDS) == frozenset(
        {"budget", "2026", "vote", "b12"}
    )


def test_jaccard_is_zero_for_empty_sets_and_one_for_identical_sets() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert jaccard(frozenset({"a"}), frozenset()) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == 1 / 3


def test_time_proximity_falls_off_linearly_and_clamps_outside_the_window() -> None:
    assert time_proximity(BASE_TIME, BASE_TIME) == 1.0
    assert time_proximity(BASE_TIME, BASE_TIME + timedelta(hours=24)) == 0.5
    assert time_proximity(BASE_TIME, BASE_TIME - timedelta(hours=24)) == 0.5
    assert time_proximity(BASE_TIME, BASE_TIME + CLUSTER_TIME_WINDOW) == 0.0
    assert time_proximity(BASE_TIME, BASE_TIME + timedelta(days=30)) == 0.0


def test_score_weights_sum_to_one_for_an_identical_simultaneous_article() -> None:
    entities = frozenset({uuid.uuid4(), uuid.uuid4()})
    left = article(title="Harbor council votes on dredging", entities=entities)
    right = article(title="Harbor council votes on dredging", entities=entities)
    assert score_articles(left, right, stop_words=STOP_WORDS) == 1.0


def test_score_is_symmetric_and_components_are_weighted_as_specified() -> None:
    shared = uuid.uuid4()
    left = article(
        title="Harbor council votes on dredging",
        entities=frozenset({shared, uuid.uuid4()}),
    )
    right = article(
        title="Council votes to delay dredging",
        hours=12,
        entities=frozenset({shared, uuid.uuid4()}),
    )
    expected = 0.5 * (3 / 7) + 0.35 * (1 / 3) + 0.15 * 0.75
    assert score_articles(left, right, stop_words=STOP_WORDS) == expected
    assert score_articles(right, left, stop_words=STOP_WORDS) == expected


def test_identical_titles_clear_the_threshold_anywhere_inside_the_window() -> None:
    # Entity extraction is optional, so a shared normalized title must be enough on its own.
    left = article(title="Harbor council votes on dredging")
    right = article(title="Harbor council votes on dredging", hours=47.9)
    assert score_articles(left, right, stop_words=STOP_WORDS) >= CLUSTER_SCORE_THRESHOLD


def test_unrelated_reporting_stays_below_the_threshold() -> None:
    left = article(title="Harbor council votes on dredging", entities=frozenset({uuid.uuid4()}))
    right = article(
        title="Regional airport announces winter schedule",
        hours=6,
        entities=frozenset({uuid.uuid4()}),
    )
    assert score_articles(left, right, stop_words=STOP_WORDS) < CLUSTER_SCORE_THRESHOLD


def test_shares_source_detects_any_overlap_in_the_discovering_feed_set() -> None:
    feed = uuid.uuid4()
    other = uuid.uuid4()
    target = article(title="Harbor council votes", feeds=frozenset({feed}))
    same_outlet = article(title="Harbor council votes", feeds=frozenset({feed, other}))
    different_outlet = article(title="Harbor council votes", feeds=frozenset({other}))
    assert shares_source(target, same_outlet) is True
    assert shares_source(target, different_outlet) is False


def test_rank_candidates_drops_same_outlet_reporting_before_scoring() -> None:
    feed = uuid.uuid4()
    target = article(title="Harbor council votes on dredging", feeds=frozenset({feed}))
    same_outlet = article(title="Harbor council votes on dredging", feeds=frozenset({feed}))
    # The same outlet covering the same story twice is not related reporting, even
    # though the identical title scores far above the threshold.
    assert score_articles(target, same_outlet, stop_words=STOP_WORDS) > CLUSTER_SCORE_THRESHOLD
    assert rank_candidates(target, [same_outlet], stop_words=STOP_WORDS) == []


def test_rank_candidates_orders_by_score_then_time_then_id() -> None:
    feed = uuid.uuid4()
    target = article(title="Harbor council votes on dredging", feeds=frozenset({feed}))
    best = article(
        title="Harbor council votes on dredging", hours=1, feeds=frozenset({uuid.uuid4()})
    )
    weaker = article(
        title="Harbor council votes on the dredging plan",
        hours=2,
        feeds=frozenset({uuid.uuid4()}),
    )
    unrelated = article(
        title="Regional airport winter schedule", hours=3, feeds=frozenset({uuid.uuid4()})
    )
    ranked = rank_candidates(target, [unrelated, weaker, best], stop_words=STOP_WORDS)
    assert [item.features.article_id for item in ranked] == [best.article_id, weaker.article_id]
    assert ranked[0].score > ranked[1].score


def test_rank_candidates_breaks_score_ties_deterministically_by_id() -> None:
    feed = uuid.uuid4()
    target = article(title="Harbor council votes on dredging", feeds=frozenset({feed}))
    first = article(
        title="Harbor council votes on dredging",
        hours=1,
        feeds=frozenset({uuid.uuid4()}),
        article_id=uuid.UUID(int=1),
    )
    second = article(
        title="Harbor council votes on dredging",
        hours=1,
        feeds=frozenset({uuid.uuid4()}),
        article_id=uuid.UUID(int=2),
    )
    ranked = rank_candidates(target, [second, first], stop_words=STOP_WORDS)
    assert [item.features.article_id for item in ranked] == [first.article_id, second.article_id]


def test_select_survivor_prefers_the_larger_cluster() -> None:
    small = ClusterSummary(id=uuid.UUID(int=1), created_at=BASE_TIME, article_count=2)
    large = ClusterSummary(
        id=uuid.UUID(int=9), created_at=BASE_TIME + timedelta(hours=5), article_count=4
    )
    assert select_survivor([small, large]) == large


def test_select_survivor_breaks_size_ties_with_the_older_cluster_then_the_lower_id() -> None:
    older = ClusterSummary(id=uuid.UUID(int=9), created_at=BASE_TIME, article_count=3)
    newer = ClusterSummary(
        id=uuid.UUID(int=1), created_at=BASE_TIME + timedelta(hours=1), article_count=3
    )
    assert select_survivor([newer, older]) == older
    same_moment = [
        ClusterSummary(id=uuid.UUID(int=7), created_at=BASE_TIME, article_count=3),
        ClusterSummary(id=uuid.UUID(int=2), created_at=BASE_TIME, article_count=3),
    ]
    assert select_survivor(same_moment).id == uuid.UUID(int=2)


def test_cluster_statistics_summarise_members_and_pick_a_stable_representative() -> None:
    feed_one, feed_two = uuid.uuid4(), uuid.uuid4()
    earliest = article(
        title="Wire report", hours=-3, feeds=frozenset({feed_one}), article_id=uuid.UUID(int=5)
    )
    tied = article(
        title="Daily report", hours=-3, feeds=frozenset({feed_two}), article_id=uuid.UUID(int=3)
    )
    latest = article(
        title="Follow up",
        hours=9,
        feeds=frozenset({feed_one, feed_two}),
        article_id=uuid.UUID(int=8),
    )
    stats = cluster_statistics([latest, earliest, tied])
    assert stats.article_count == 3
    assert stats.source_count == 2
    assert stats.first_published_at == BASE_TIME - timedelta(hours=3)
    assert stats.last_published_at == BASE_TIME + timedelta(hours=9)
    assert stats.representative_article_id == uuid.UUID(int=3)


def test_rule_clusterer_satisfies_the_replaceable_interface() -> None:
    clusterer = RuleClusterer()
    assert isinstance(clusterer, StoryClusterer)
    assert clusterer.version == "rule-2"


# Two outlets rewording one story: different headlines, no shared entities, but the same
# specifics in their opening paragraphs.
HARBOUR_WIRE = (
    "Harbour dredging halted after contractor walks off job. Work to deepen the shipping"
    " channel at the old harbour stopped on Tuesday when the dredging contractor pulled its"
    " crews and barges, citing unpaid invoices. Port officials said the channel deepening,"
    " meant to let larger container ships dock, is now weeks behind schedule."
)
HARBOUR_DAILY = (
    "Port deepening project stalls in payment dispute. Barges left the harbour mouth on"
    " Tuesday as the firm hired to dredge the shipping channel downed tools over unpaid"
    " bills, leaving the deepening scheme for bigger container vessels behind schedule."
)
CYCLE_LANES = (
    "Council approves new cycle lanes for city centre. The city council on Tuesday approved"
    " a network of protected cycle lanes through the centre, with work due to start in"
    " spring. Councillors said the lanes would cut traffic and improve safety."
)
LEDE_STOP_WORDS = frozenset({"after", "and", "at", "for", "in", "its", "of", "on", "the", "to"})
# Words most articles in a window share; every other term is rare there.
COMMON_TERMS = ("said", "tuesday", "work", "new", "city")


def _window_weights(*texts: str, window_articles: int = 1000) -> dict[str, float]:
    terms = frozenset().union(*(lede_terms(text, LEDE_STOP_WORDS) for text in texts))
    frequencies = {term: 400 if term in COMMON_TERMS else 2 for term in terms}
    return term_weights(frequencies, window_articles)


def test_stem_folds_common_inflections_onto_one_term() -> None:
    assert stem("votes") == stem("voted") == stem("voting") == stem("vote")
    assert stem("deepening") == stem("deepen")
    assert stem("barges") == stem("barge")
    assert stem("invoices") == stem("invoice")
    assert stem("planned") == stem("plan")
    assert stem("parties") == stem("party")
    assert stem("called") == "call"
    assert stem("crisis") == "crisis"


def test_lede_terms_keep_the_opening_words_without_stop_words_numbers_or_short_words() -> None:
    terms = lede_terms("The 3 barges of Harbour: on 2026-09-02 dredging stops", LEDE_STOP_WORDS)
    assert terms == frozenset({"barg", "harbour", "dredg", "stop"})
    assert lede_terms("alpha bravo charlie delta", frozenset(), word_limit=2) == frozenset(
        {"alpha", "bravo"}
    )


def test_term_weights_scale_inverse_document_frequency_into_the_unit_interval() -> None:
    weights = term_weights({"barg": 1, "port": 30, "said": 1000}, 1000)
    assert weights["barg"] == 1.0
    assert weights["said"] == 0.0
    assert 0.0 < weights["port"] < 1.0
    # A small window is measured as if it held the minimum, so a pair alone in it still
    # weighs its shared terms as rare.
    small = term_weights({"barg": 2}, 3)
    assert small == term_weights({"barg": 2}, TERM_WEIGHT_MINIMUM_WINDOW)
    assert small["barg"] > 0.8


def test_term_overlap_needs_a_minimum_of_shared_terms() -> None:
    shared = frozenset(f"term{index}" for index in range(CLUSTER_MINIMUM_SHARED_TERMS - 1))
    weights = dict.fromkeys(shared, 1.0)
    assert term_overlap(shared, shared, weights) == 0.0


def test_term_overlap_divides_by_the_smaller_set_within_a_floor_and_a_ceiling() -> None:
    def terms(count: int, prefix: str = "t") -> frozenset[str]:
        return frozenset(f"{prefix}{index}" for index in range(count))

    weights = dict.fromkeys(terms(60), 1.0)
    # Two short summaries sharing everything still count against the floor.
    assert term_overlap(terms(6), terms(6), weights) == 6 / TERM_OVERLAP_FLOOR
    assert term_overlap(terms(6), terms(40), weights) == 6 / TERM_OVERLAP_FLOOR
    # Between floor and ceiling, the smaller set is the denominator.
    assert term_overlap(terms(20), terms(40), weights) == 1.0
    assert term_overlap(terms(10) | terms(10, "x"), terms(40), weights) == 10 / 20
    # Long ledes need no more than the ceiling's worth of shared weight.
    assert term_overlap(terms(50), terms(50), weights) == 1.0
    assert term_overlap(terms(20) | terms(30, "x"), terms(60), weights) == (
        20 / TERM_OVERLAP_CEILING
    )


def test_a_reworded_story_clears_the_threshold_on_shared_opening_wording() -> None:
    weights = _window_weights(HARBOUR_WIRE, HARBOUR_DAILY)
    wire = article(
        title="Harbour dredging halted after contractor walks off job",
        entities=frozenset({uuid.uuid4()}),
        terms=lede_terms(HARBOUR_WIRE, LEDE_STOP_WORDS),
    )
    daily = article(
        title="Port deepening project stalls in payment dispute",
        hours=6,
        entities=frozenset({uuid.uuid4()}),
        terms=lede_terms(HARBOUR_DAILY, LEDE_STOP_WORDS),
        title_hash="hash-b",
    )
    assert title_tokens(wire.title, STOP_WORDS) & title_tokens(daily.title, STOP_WORDS) == set()
    # The headline rule alone misses this pair: nothing but time is shared.
    assert score_articles(wire, daily, stop_words=STOP_WORDS) < CLUSTER_SCORE_THRESHOLD
    score = score_articles(wire, daily, stop_words=STOP_WORDS, weights=weights)
    assert score >= CLUSTER_SCORE_THRESHOLD
    assert score_articles(daily, wire, stop_words=STOP_WORDS, weights=weights) == score


def test_a_different_story_sharing_only_common_words_stays_below_the_threshold() -> None:
    weights = _window_weights(HARBOUR_WIRE, CYCLE_LANES)
    wire = article(title="Harbour dredging halted", terms=lede_terms(HARBOUR_WIRE, LEDE_STOP_WORDS))
    lanes = article(
        title="Council approves new cycle lanes",
        terms=lede_terms(CYCLE_LANES, LEDE_STOP_WORDS),
        title_hash="hash-b",
    )
    assert len(wire.terms & lanes.terms) >= 1
    assert score_articles(wire, lanes, stop_words=STOP_WORDS, weights=weights) < (
        CLUSTER_SCORE_THRESHOLD
    )


def test_wording_never_lowers_the_headline_score() -> None:
    entities = frozenset({uuid.uuid4(), uuid.uuid4()})
    left = article(title="Harbor council votes on dredging", entities=entities)
    right = article(title="Harbor council votes on dredging", entities=entities)
    headline = score_articles(left, right, stop_words=STOP_WORDS)
    assert score_articles(left, right, stop_words=STOP_WORDS, weights={"x": 1.0}) == headline


def test_rank_candidates_finds_rewordings_only_with_window_weights() -> None:
    weights = _window_weights(HARBOUR_WIRE, HARBOUR_DAILY)
    target = article(
        title="Harbour dredging halted after contractor walks off job",
        feeds=frozenset({uuid.uuid4()}),
        terms=lede_terms(HARBOUR_WIRE, LEDE_STOP_WORDS),
    )
    reworded = article(
        title="Port deepening project stalls in payment dispute",
        hours=2,
        feeds=frozenset({uuid.uuid4()}),
        terms=lede_terms(HARBOUR_DAILY, LEDE_STOP_WORDS),
    )
    assert rank_candidates(target, [reworded], stop_words=STOP_WORDS) == []
    ranked = rank_candidates(target, [reworded], stop_words=STOP_WORDS, weights=weights)
    assert [item.features.article_id for item in ranked] == [reworded.article_id]
