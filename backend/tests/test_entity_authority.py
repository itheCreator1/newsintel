"""Authority rules that need no database: what may be merged, and how article rows combine."""

import uuid

import pytest

from app.entities.authority import (
    EntityRef,
    RowPart,
    combine_rows,
    merge_problem,
    split_occurrences,
)


def _ref(entity_type: str = "PERSON", authority_id: uuid.UUID | None = None) -> EntityRef:
    return EntityRef(id=uuid.uuid4(), entity_type=entity_type, authority_id=authority_id)


def _occurrence(start: int, observed: uuid.UUID | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "section": "title",
        "reference_id": None,
        "start": start,
        "end": start + 3,
        "input_start": start,
        "input_end": start + 3,
    }
    if observed is not None:
        value["entity_id"] = str(observed)
    return value


def test_a_root_may_take_a_variant_of_the_same_type() -> None:
    assert merge_problem(_ref(), _ref(), distinct=False) is None


def test_an_entity_cannot_be_merged_into_itself() -> None:
    entity = _ref()
    assert merge_problem(entity, entity, distinct=False) == (
        422,
        "An entity cannot be merged into itself",
    )


def test_a_pair_marked_distinct_is_not_merged() -> None:
    assert merge_problem(_ref(), _ref(), distinct=True) == (
        409,
        "These entities are marked as different",
    )


def test_merge_keeps_one_level_so_a_variant_must_be_split_first() -> None:
    root = _ref()
    other = _ref()
    variant = _ref(authority_id=root.id)
    assert merge_problem(variant, other, distinct=False) == (
        409,
        "This entity is a variant of another; split it first",
    )
    assert merge_problem(variant, root, distinct=False) == (
        409,
        "This entity is already a variant of that one",
    )


def test_merge_rejects_different_types_except_places() -> None:
    assert merge_problem(_ref("PERSON"), _ref("ORG"), distinct=False) == (
        422,
        "Only entities of the same type can be merged",
    )
    # Decided 2026-10-08: a country the model tagged LOC may join its GPE root.
    assert merge_problem(_ref("LOCATION"), _ref("GPE"), distinct=False) is None
    assert merge_problem(_ref("GPE"), _ref("LOCATION"), distinct=False) is None


def test_combine_rows_sums_occurrences_and_tags_each_with_the_name_it_came_from() -> None:
    root, variant = uuid.uuid4(), uuid.uuid4()
    combined = combine_rows(
        root,
        [
            RowPart(None, "GPE", [_occurrence(10)]),
            RowPart(variant, "LOC", [_occurrence(30), _occurrence(2)]),
        ],
    )

    assert combined.occurrence_count == 3
    assert combined.relevance == pytest.approx(3 / 5)
    assert [item["input_start"] for item in combined.occurrences] == [2, 10, 30]
    assert [item.get("entity_id") for item in combined.occurrences] == [
        str(variant),
        None,
        str(variant),
    ]
    # Two of three mentions came from the variant: the row records it as the name used.
    assert combined.observed_entity_id == variant
    assert combined.original_label == "LOC"


def test_combine_rows_prefers_the_root_on_a_tie_and_caps_relevance() -> None:
    root, variant = uuid.uuid4(), uuid.uuid4()
    combined = combine_rows(
        root,
        [
            RowPart(None, "PERSON", [_occurrence(index) for index in range(0, 30, 10)]),
            RowPart(variant, "PERSON", [_occurrence(index) for index in range(1, 31, 10)]),
        ],
    )

    assert combined.observed_entity_id is None
    assert combined.relevance == 1.0


def test_combine_rows_keeps_tags_already_on_an_occurrence() -> None:
    root, variant, older = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    combined = combine_rows(root, [RowPart(variant, "PERSON", [_occurrence(5, older)])])

    assert combined.occurrences[0]["entity_id"] == str(older)
    assert combined.observed_entity_id == older


def test_combine_rows_counts_rows_stored_without_offsets() -> None:
    root, variant = uuid.uuid4(), uuid.uuid4()
    combined = combine_rows(root, [RowPart(None, "ORG", [], 1), RowPart(variant, "ORG", [], 2)])

    assert combined.occurrence_count == 3
    assert combined.observed_entity_id == variant


def test_split_occurrences_takes_back_only_the_variant() -> None:
    variant, other = uuid.uuid4(), uuid.uuid4()
    occurrences = [_occurrence(1, variant), _occurrence(2), _occurrence(3, other)]

    taken, kept = split_occurrences(occurrences, variant)

    # Back on its own root the variant's mentions need no tag.
    assert taken == [_occurrence(1)]
    assert kept == [_occurrence(2), _occurrence(3, other)]
