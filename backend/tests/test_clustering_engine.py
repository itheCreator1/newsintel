import uuid
from datetime import UTC, datetime, timedelta

from app.clustering.engine import (
    CLUSTER_SCORE_THRESHOLD,
    CLUSTER_TIME_WINDOW,
    ArticleFeatures,
    ClusterSummary,
    RuleClusterer,
    StoryClusterer,
    cluster_statistics,
    jaccard,
    rank_candidates,
    score_articles,
    select_survivor,
    shares_source,
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
) -> ArticleFeatures:
    return ArticleFeatures(
        article_id=article_id or uuid.uuid4(),
        title=title,
        normalized_title_hash=title_hash,
        effective_date=BASE_TIME + timedelta(hours=hours),
        entity_ids=entities,
        feed_ids=feeds,
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
    assert clusterer.version == "rule-1"
