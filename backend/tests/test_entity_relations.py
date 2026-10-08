"""See-also rules that need no database: labels and their inverses, types, dates, symmetry."""

import uuid

import pytest
from test_entity_authority import _ref

from app.entities.authority import merge_problem
from app.entities.relations import (
    LABELS,
    RelationError,
    check_dates,
    labels_for,
    oriented,
    resolve_label,
    type_problem,
)


def test_every_label_names_one_type_and_a_direction() -> None:
    assert resolve_label("later_name") == ("succeeded_by", False)
    assert resolve_label("earlier_name") == ("succeeded_by", True)
    assert resolve_label("part_of") == ("part_of", False)
    assert resolve_label("has_part") == ("part_of", True)
    assert resolve_label("member_of") == ("member_of", False)
    assert resolve_label("has_member") == ("member_of", True)
    assert resolve_label("leader_of") == ("leader_of", False)
    assert resolve_label("led_by") == ("leader_of", True)
    assert resolve_label("related") == ("related", False)
    with pytest.raises(RelationError) as refused:
        resolve_label("married_to")
    assert refused.value.status_code == 422


def test_the_label_is_read_from_the_side_that_asks() -> None:
    # Facebook succeeded_by Meta: on Facebook's page Meta is the later name; the reverse on Meta's.
    assert LABELS[("succeeded_by", True)] == "later_name"
    assert LABELS[("succeeded_by", False)] == "earlier_name"
    assert LABELS[("related", True)] == LABELS[("related", False)] == "related"


def test_types_follow_the_table() -> None:
    assert type_problem("succeeded_by", "ORG", "ORG") is None
    assert type_problem("succeeded_by", "GPE", "LOCATION") is None
    assert type_problem("succeeded_by", "ORG", "GPE") is not None
    assert type_problem("succeeded_by", "PERSON", "PERSON") is not None
    assert type_problem("part_of", "ORG", "ORG") is None
    assert type_problem("part_of", "LOCATION", "GPE") is None
    assert (
        type_problem("part_of", "PERSON", "PERSON") == "A person cannot be part of another entity"
    )
    assert type_problem("member_of", "PERSON", "ORG") is None
    assert type_problem("member_of", "GPE", "ORG") is None
    assert type_problem("member_of", "PERSON", "GPE") is not None
    assert type_problem("leader_of", "PERSON", "GPE") is None
    assert type_problem("leader_of", "ORG", "ORG") is not None
    assert type_problem("related", "EVENT", "PRODUCT") is None


def test_an_entity_is_offered_only_the_labels_its_type_can_take() -> None:
    assert labels_for("PERSON") == ["member_of", "leader_of", "related"]
    assert labels_for("ORG") == [
        "later_name",
        "earlier_name",
        "part_of",
        "has_part",
        "member_of",
        "has_member",
        "led_by",
        "related",
    ]
    assert labels_for("EVENT") == ["related"]


@pytest.mark.parametrize("value", ["2009", "2021-10", "2021-10-28", None])
def test_partial_dates_are_accepted(value: str | None) -> None:
    check_dates(value, None)


@pytest.mark.parametrize(
    "value", ["21", "2021-13", "2021-02-30", "2021/10", "2021-1", "2021-10-28T00"]
)
def test_malformed_dates_are_refused(value: str) -> None:
    with pytest.raises(RelationError) as refused:
        check_dates(value, None)
    assert refused.value.status_code == 422


def test_the_period_may_not_end_before_it_starts_at_any_precision() -> None:
    check_dates("2021-10", "2021")
    check_dates("2021", "2021-03-01")
    check_dates("2020-12-31", "2021")
    with pytest.raises(RelationError):
        check_dates("2021-10", "2021-09-30")
    with pytest.raises(RelationError):
        check_dates("2022", "2021-12")


def test_related_is_stored_once_per_pair() -> None:
    low, high = sorted((uuid.uuid4(), uuid.uuid4()))
    assert oriented("related", high, low) == (low, high)
    assert oriented("related", low, high) == (low, high)
    assert oriented("member_of", high, low) == (high, low)


def test_linked_entities_are_not_merged() -> None:
    assert merge_problem(_ref(), _ref(), distinct=False, linked=True) == (
        409,
        "These entities are linked by a see-also relation; remove it first",
    )
