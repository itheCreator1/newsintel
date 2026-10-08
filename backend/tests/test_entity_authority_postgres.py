import asyncio
import os
import uuid

import pytest
from event_fixtures import story
from sqlalchemy import select

from app.db.session import session_factory
from app.events.models import EventEntity
from app.events.service import associate_cluster, create_event, refresh_event
from app.feeds.models import Article
from app.nlp.models import (
    ArticleEntity,
    ArticleNlpState,
    Entity,
    EntityAuthorityChange,
    EntityAuthorityRun,
    NlpJob,
    NlpProcessorRun,
)
from app.search.models import ArticleSearchState

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

DIGEST = "a" * 64


def _occurrences(*starts: int) -> list[dict[str, object]]:
    return [
        {
            "section": "title",
            "reference_id": None,
            "start": start,
            "end": start + 4,
            "input_start": start,
            "input_end": start + 4,
        }
        for start in starts
    ]


async def _entity(db, name: str, entity_type: str = "PERSON", **values: object) -> Entity:  # type: ignore[no-untyped-def]
    entity = Entity(
        language="en",
        entity_type=entity_type,
        normalized_text=f"{name.casefold()} {uuid.uuid4().hex}",
        display_text=name,
        **values,
    )
    db.add(entity)
    await db.flush()
    return entity


async def _article(db, rows: list[tuple[Entity, list[int]]]) -> uuid.UUID:  # type: ignore[no-untyped-def]
    """An article whose current entities job found each entity at the given offsets."""
    article = Article(
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title="Authority evidence",
        normalized_title_hash=uuid.uuid4().hex,
    )
    db.add(article)
    await db.flush()
    state = ArticleNlpState(
        article_id=article.id,
        processor_name="entities",
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(state)
    await db.flush()
    job = NlpJob(
        state_id=state.id,
        article_id=article.id,
        processor_name="entities",
        generation=1,
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(job)
    await db.flush()
    run = NlpProcessorRun(
        job_id=job.id,
        article_id=article.id,
        processor_name="entities",
        processor_version="test",
        algorithm_version="test",
        configuration_fingerprint=DIGEST,
        input_fingerprint=DIGEST,
        generation=1,
        outcome="success",
    )
    db.add(run)
    await db.flush()
    for entity, starts in rows:
        db.add(
            ArticleEntity(
                article_id=article.id,
                entity_id=entity.id,
                run_id=run.id,
                original_label=entity.entity_type,
                occurrence_count=len(starts),
                relevance=min(1.0, len(starts) / 5),
                occurrences=_occurrences(*starts),
                input_fingerprint=DIGEST,
                is_current=True,
            )
        )
    await db.flush()
    return article.id


async def _rows(article_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID | None, int]]:
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(
                    ArticleEntity.entity_id,
                    ArticleEntity.observed_entity_id,
                    ArticleEntity.occurrence_count,
                )
                .where(ArticleEntity.article_id == article_id, ArticleEntity.is_current.is_(True))
                .order_by(ArticleEntity.occurrence_count.desc())
            )
        ).all()
    return [tuple(row) for row in rows]


async def _finish(run_id: uuid.UUID, batch_size: int = 100) -> int:
    """Advance a run until it finishes; returns how many batches that took."""
    from app.entities.authority import advance_run

    batches = 0
    while True:
        async with session_factory() as db, db.begin():
            run = await db.get(EntityAuthorityRun, run_id)
            assert run is not None
            if run.status != "running":
                return batches
            await advance_run(db, run_id, batch_size=batch_size)
        batches += 1
        assert batches < 50


async def test_merge_points_variants_to_the_root_without_chains() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Alexis Tsipras")
        variant = await _entity(db, "Tsipras")
        # The variant already has a variant of its own.
        older = await _entity(db, "A. Tsipras")
        older.authority_id = variant.id
        await db.flush()
        ids = root.id, variant.id, older.id

    async with session_factory() as db, db.begin():
        await merge(db, variant_id=ids[1], target_id=ids[0])

    async with session_factory() as db:
        stored = {
            entity.id: entity.authority_id
            for entity in await db.scalars(select(Entity).where(Entity.id.in_(ids)))
        }
    # Both point straight at the root: never variant -> variant -> root.
    assert stored == {ids[0]: None, ids[1]: ids[0], ids[2]: ids[0]}


async def test_merge_into_a_variant_lands_on_its_root() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Kyriakos Mitsotakis")
        known = await _entity(db, "Mitsotakis", authority_id=root.id)
        new = await _entity(db, "K. Mitsotakis")
        ids = root.id, known.id, new.id

    async with session_factory() as db, db.begin():
        await merge(db, variant_id=ids[2], target_id=ids[1])

    async with session_factory() as db:
        assert (await db.get(Entity, ids[2])).authority_id == ids[0]  # type: ignore[union-attr]


async def test_merge_rejects_a_pair_marked_distinct() -> None:
    from app.entities.authority import AuthorityError, add_distinct, merge

    async with session_factory() as db, db.begin():
        first = await _entity(db, "John Smith")
        second = await _entity(db, "John Smith")
        ids = first.id, second.id
        await add_distinct(db, ids[0], ids[1])

    async with session_factory() as db:
        with pytest.raises(AuthorityError) as failure:
            async with db.begin():
                await merge(db, variant_id=ids[1], target_id=ids[0])
    assert failure.value.status_code == 409
    async with session_factory() as db:
        assert (await db.get(Entity, ids[1])).authority_id is None  # type: ignore[union-attr]


async def test_merge_run_moves_links_in_batches_and_merges_rows_of_one_article() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "United States", "GPE")
        variant = await _entity(db, "America", "GPE")
        only_variant = [await _article(db, [(variant, [0, 20])]) for _ in range(3)]
        both = await _article(db, [(root, [5]), (variant, [40, 60])])
        untouched = await _article(db, [(root, [7])])
        root_id, variant_id = root.id, variant.id

    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id

    # Four articles, two per batch, then one empty batch that finishes the run.
    assert await _finish(run_id, batch_size=2) == 3
    for article_id in only_variant:
        assert await _rows(article_id) == [(root_id, variant_id, 2)]
    # One row per article and root: its own mention and the variant's two.
    assert await _rows(both) == [(root_id, variant_id, 3)]
    assert await _rows(untouched) == [(root_id, None, 1)]
    async with session_factory() as db:
        run = await db.get(EntityAuthorityRun, run_id)
        assert run is not None and run.status == "finished" and run.finished_at is not None


async def test_merge_run_refreshes_the_events_of_moved_articles() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Harbour Authority", "ORG")
        variant = await _entity(db, "Port Authority", "ORG")
        first = await story(db, [root], articles=2)
        second = await story(db, [variant], articles=1)
        event = await create_event(db, "authority-test")
        await associate_cluster(db, event, first.id, 1)
        await associate_cluster(db, event, second.id, 1)
        await refresh_event(db, event)
        event_id, root_id, variant_id = event.id, root.id, variant.id

    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id
    await _finish(run_id)

    async with session_factory() as db:
        counts = {
            row.entity_id: row.article_count
            for row in await db.scalars(select(EventEntity).where(EventEntity.event_id == event_id))
        }
    assert counts == {root_id: 3}


async def test_merge_run_requests_indexing_only_for_affected_articles() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Ada Reyes")
        variant = await _entity(db, "A. Reyes")
        moved = await _article(db, [(variant, [0])])
        other = await _article(db, [(root, [0])])
        root_id, variant_id = root.id, variant.id

    async def revision(article_id: uuid.UUID) -> int:
        async with session_factory() as db:
            state = await db.get(ArticleSearchState, article_id)
            return state.requested_revision if state else 0

    before = await revision(moved), await revision(other)
    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id
    await _finish(run_id)

    assert await revision(moved) == before[0] + 1
    assert await revision(other) == before[1]


async def test_split_restores_the_observed_links() -> None:
    from app.entities.authority import merge, split

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Bo Lind")
        variant = await _entity(db, "B. Lind")
        both = await _article(db, [(root, [5]), (variant, [40, 60])])
        only_variant = await _article(db, [(variant, [9])])
        root_id, variant_id = root.id, variant.id

    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        merge_run = run.id
    await _finish(merge_run)
    async with session_factory() as db, db.begin():
        run = await split(db, variant_id)
        split_run = run.id
    await _finish(split_run)

    async with session_factory() as db:
        assert (await db.get(Entity, variant_id)).authority_id is None  # type: ignore[union-attr]
    # Exactly as before the merge: the mentions go back to the name they came from.
    assert await _rows(both) == [(variant_id, None, 2), (root_id, None, 1)]
    assert await _rows(only_variant) == [(variant_id, None, 1)]


async def test_split_rejects_a_root() -> None:
    from app.entities.authority import AuthorityError, split

    async with session_factory() as db, db.begin():
        root_id = (await _entity(db, "Mara Voss")).id

    async with session_factory() as db:
        with pytest.raises(AuthorityError) as failure:
            await split(db, root_id)
    assert failure.value.status_code == 409


async def test_history_records_each_change() -> None:
    from app.entities.authority import (
        add_distinct,
        merge,
        remove_distinct,
        rename,
        set_ambiguous,
        set_status,
        split,
    )

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Ivo Kestrel")
        variant = await _entity(db, "I. Kestrel")
        other = await _entity(db, "Ivo Kestrel")
        ids = root.id, variant.id, other.id

    async with session_factory() as db, db.begin():
        await merge(db, variant_id=ids[1], target_id=ids[0])
        await rename(db, ids[0], "Kestrel, Ivo")
        await set_status(db, ids[0], "established")
        await set_ambiguous(db, ids[0], True)
        await add_distinct(db, ids[0], ids[2])
        await remove_distinct(db, ids[0], ids[2])
        await split(db, ids[1])

    async with session_factory() as db:
        changes = list(
            await db.scalars(
                select(EntityAuthorityChange)
                .where(EntityAuthorityChange.entity_id.in_(ids))
                .order_by(EntityAuthorityChange.created_at, EntityAuthorityChange.id)
            )
        )
    assert [change.action for change in changes] == [
        "merged",
        "renamed",
        "status_changed",
        "ambiguous_changed",
        "distinct_added",
        "distinct_removed",
        "split",
    ]
    merged = changes[0]
    assert (merged.entity_id, merged.other_id) == (ids[1], ids[0])
    assert merged.before == {"authority_id": None}
    assert merged.after == {"authority_id": str(ids[0])}
    assert changes[1].before == {"preferred_text": None}
    assert changes[1].after == {"preferred_text": "Kestrel, Ivo"}


async def test_preferred_name_and_status_belong_to_the_root() -> None:
    from app.entities.authority import AuthorityError, rename, set_status

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Lena Holt")
        variant_id = (await _entity(db, "L. Holt", authority_id=root.id)).id

    async with session_factory() as db:
        with pytest.raises(AuthorityError) as failure:
            await rename(db, variant_id, "Holt, Lena")
        assert failure.value.status_code == 409
        with pytest.raises(AuthorityError):
            await set_status(db, variant_id, "established")


async def test_scheduler_advances_unfinished_runs() -> None:
    from app.entities.authority import merge, scan_authority_runs

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Grid Co", "ORG")
        variant = await _entity(db, "Grid Company", "ORG")
        article_id = await _article(db, [(variant, [0])])
        root_id, variant_id = root.id, variant.id
    async with session_factory() as db, db.begin():
        run_id = (await merge(db, variant_id=variant_id, target_id=root_id)).id

    for _ in range(3):
        await scan_authority_runs()

    assert await _rows(article_id) == [(root_id, variant_id, 1)]
    async with session_factory() as db:
        assert (await db.get(EntityAuthorityRun, run_id)).status == "finished"  # type: ignore[union-attr]


async def test_merge_and_nlp_on_the_same_article_serialise(monkeypatch: pytest.MonkeyPatch) -> None:
    """NER upserting the variant waits for the merge's row lock and then writes the root."""
    from test_nlp_jobs_postgres import ENGLISH_DESCRIPTION, _run_entities_job

    from app.entities.authority import merge

    token = uuid.uuid4().hex[:8]
    name = f"Nadia Orlov {token}"
    async with session_factory() as db, db.begin():
        root = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"n. orlov {token}",
            display_text=f"N. Orlov {token}",
        )
        variant = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=name.casefold(),
            display_text=name,
        )
        db.add_all([root, variant])
        await db.flush()
        root_id, variant_id = root.id, variant.id

    merging = session_factory()
    await merging.begin()
    await merge(merging, variant_id=variant_id, target_id=root_id)
    job = asyncio.create_task(
        _run_entities_job(
            monkeypatch,
            title=f"{name} announced a new wage plan for public sector workers today",
            description=ENGLISH_DESCRIPTION,
            spans=[(name, "PERSON")],
        )
    )
    await asyncio.sleep(1.5)
    # The upsert of the variant's row is waiting on the merge's lock.
    assert not job.done()
    await merging.commit()
    await merging.close()
    _, article_id, _ = await job

    assert await _rows(article_id) == [(root_id, variant_id, 1)]


async def test_resolver_maps_any_name_to_its_root_and_expands_a_root_for_search() -> None:
    from app.entities.authority import expand_for_search, resolve_root

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Wim Basso")
        first = await _entity(db, "W. Basso", authority_id=root.id)
        second = await _entity(db, "Basso", authority_id=root.id)
        alone = await _entity(db, "Zora Quint")
        ids = root.id, first.id, second.id, alone.id

    unknown = uuid.uuid4()
    group = sorted(str(value) for value in ids[:3])
    async with session_factory() as db:
        assert [await resolve_root(db, value) for value in ids] == [ids[0], ids[0], ids[0], ids[3]]
        assert await resolve_root(db, unknown) is None
        # Root and every variant: articles still indexed under a variant's id are found too.
        assert await expand_for_search(db, [str(ids[1])]) == group
        assert await expand_for_search(db, [str(ids[0]), str(ids[3])]) == sorted(
            [*group, str(ids[3])]
        )
        # An id the archive does not know stays in the filter (it matches nothing).
        assert await expand_for_search(db, [str(unknown)]) == [str(unknown)]
        assert await expand_for_search(db, []) == []
