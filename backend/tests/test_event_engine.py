import uuid
from datetime import UTC, datetime, timedelta

from app.events.engine import (
    EVENT_ALGORITHM_VERSION,
    EVENT_SCORE_THRESHOLD,
    EVENT_TIME_WINDOW,
    ClusterFacts,
    EventAssociator,
    EventFacts,
    RuleEventAssociator,
    choose_event,
    score_cluster,
    time_gap,
)

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)
E1, E2, E3 = (uuid.UUID(int=i) for i in (1, 2, 3))
FIRE = frozenset({"fire", "harbour", "warehouse"})


def cluster(
    *, hours=0.0, hours_to=None, entities=frozenset(), title=frozenset(), country=None
) -> ClusterFacts:
    start = BASE + timedelta(hours=hours)
    end = BASE + timedelta(hours=hours if hours_to is None else hours_to)
    return ClusterFacts(uuid.uuid4(), start, end, title, frozenset(entities), country)


def event(
    *, id=E1, hours=0.0, hours_to=None, entities=frozenset(), titles=(), country=None
) -> EventFacts:
    start = BASE + timedelta(hours=hours)
    end = BASE + timedelta(hours=hours if hours_to is None else hours_to)
    return EventFacts(id, start, end, frozenset(entities), tuple(titles), country)


def ids(*n: int) -> frozenset[uuid.UUID]:
    return frozenset(uuid.UUID(int=1000 + i) for i in n)


def test_the_version_is_recorded_and_the_engine_is_replaceable() -> None:
    assert EVENT_ALGORITHM_VERSION == "rule-1"
    assert RuleEventAssociator().version == EVENT_ALGORITHM_VERSION

    class Other:
        version = "rule-2"

        async def run_batch(self, db, limit):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    assert isinstance(RuleEventAssociator(), EventAssociator)
    assert isinstance(Other(), EventAssociator)


def test_time_gap_is_zero_when_spans_overlap_and_grows_outside() -> None:
    e = event(hours=0, hours_to=10)
    assert time_gap(cluster(hours=5, hours_to=20), e) == timedelta(0)
    assert time_gap(cluster(hours=12, hours_to=14), e) == timedelta(hours=2)
    assert time_gap(cluster(hours=-7, hours_to=-3), e) == timedelta(hours=3)


def test_each_signal_is_scored_on_its_own() -> None:
    e = event(entities=ids(1, 2), titles=[FIRE], country="GR")
    same = score_cluster(cluster(entities=ids(1, 2), title=FIRE, country="GR"), e)
    assert same.signals == {"time": 1.0, "entities": 1.0, "title": 1.0, "location": 1.0}
    assert abs(same.total - 1.0) < 1e-9

    # Only the entity signal differs.
    half = score_cluster(cluster(entities=ids(1, 3), title=FIRE, country="GR"), e)
    assert half.signals["entities"] == 1 / 3 and half.signals["title"] == 1.0

    # Time decays linearly with the gap and reaches zero at the window.
    far = score_cluster(cluster(hours=EVENT_TIME_WINDOW.total_seconds() / 3600), e)
    assert far.signals["time"] == 0.0
    mid = score_cluster(cluster(hours=EVENT_TIME_WINDOW.total_seconds() / 7200), e)
    assert abs(mid.signals["time"] - 0.5) < 1e-9

    # Title uses the closest member cluster, not the union.
    e_two = event(titles=[FIRE, frozenset({"budget", "vote"})])
    assert score_cluster(cluster(title=FIRE), e_two).signals["title"] == 1.0


def test_an_unknown_or_different_country_is_never_a_reward_and_unknown_is_not_a_penalty() -> None:
    e = event(country="GR")
    assert score_cluster(cluster(country="GR"), e).signals["location"] == 1.0
    assert score_cluster(cluster(country="TR"), e).signals["location"] == 0.0
    assert score_cluster(cluster(country=None), e).signals["location"] == 0.0
    assert score_cluster(cluster(country="GR"), event(country=None)).signals["location"] == 0.0
    # Unknown and different score the same, so an unknown country cannot decide a match.
    assert (
        score_cluster(cluster(country=None), e).total
        == score_cluster(cluster(country="TR"), e).total
    )


def test_an_event_without_a_span_scores_no_time() -> None:
    empty = EventFacts(E1, None, None, ids(1), (), None)
    assert score_cluster(cluster(entities=ids(1)), empty).signals["time"] == 0.0
    assert time_gap(cluster(), empty) is None


def test_the_threshold_boundary_is_inclusive() -> None:
    # Tune the entity overlap so the total lands exactly on, then just under, the threshold.
    e = event(entities=ids(*range(10)))
    at = None
    for shared in range(0, 11):
        c = cluster(entities=ids(*range(shared)) | ids(*range(100, 100 + 10 - shared)))
        total = score_cluster(c, e).total
        picked = choose_event(c, [e])
        assert (picked is not None) == (total >= EVENT_SCORE_THRESHOLD)
        if picked is not None and at is None:
            at = shared
    assert at is not None and 0 < at < 10


def test_the_best_score_wins_then_the_smaller_gap_then_the_smaller_id() -> None:
    c = cluster(hours=0, entities=ids(1, 2, 3), title=FIRE)
    strong = event(id=E3, entities=ids(1, 2, 3), titles=[FIRE])
    weak = event(id=E1, entities=ids(1, 2), titles=[FIRE])
    picked = choose_event(c, [weak, strong])
    assert picked is not None and picked[0].id == E3

    # Equal scores: the event with the smaller time gap wins, whatever the input order.
    near = event(id=E2, hours=0, hours_to=1, entities=ids(1, 2, 3), titles=[FIRE])
    near_twin = event(id=E1, hours=0, hours_to=1, entities=ids(1, 2, 3), titles=[FIRE])
    for order in ([near, near_twin], [near_twin, near]):
        picked = choose_event(c, order)
        assert picked is not None and picked[0].id == E1


def test_a_cluster_stays_in_its_current_event_while_it_still_qualifies() -> None:
    c = cluster(entities=ids(1, 2, 3), title=FIRE)
    current = event(id=E2, entities=ids(1, 2, 3), titles=[FIRE])
    better = event(id=E1, entities=ids(1, 2, 3), titles=[FIRE], country=None)
    picked = choose_event(c, [better], current=current)
    assert picked is not None and picked[0].id == E2

    # Once the current event no longer qualifies, the best other candidate takes it.
    stale = event(id=E2, hours=500, entities=ids(9))
    picked = choose_event(c, [better], current=stale)
    assert picked is not None and picked[0].id == E1
    assert choose_event(c, [], current=stale) is None


def test_the_same_facts_give_the_same_choice() -> None:
    c = cluster(entities=ids(1, 2), title=FIRE, country="GR")
    candidates = [event(id=uuid.UUID(int=i), entities=ids(1, 2, i)) for i in range(1, 6)]
    first = choose_event(c, candidates)
    assert first is not None
    for _ in range(3):
        assert choose_event(c, list(reversed(candidates))) == first
