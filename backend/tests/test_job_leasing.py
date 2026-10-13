"""Claims, leases and retries, written once for every queue table (fix plan Δ2α).

Each queue (NLP, clustering, article processing, search deliveries) had its own copy of the claim
checks, the failure bookkeeping and the lease heartbeat; the copies had started to drift.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.jobs.leasing import (
    Retries,
    claim,
    claimable,
    heartbeat,
    heartbeat_interval,
    holds,
    record_failure,
    release,
    renew,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
RETRIES = Retries(base_seconds=30, cap_seconds=900, max_attempts=5)


def _row(**values: object) -> SimpleNamespace:
    row = SimpleNamespace(
        status="queued",
        claim_token=None,
        claim_expires_at=None,
        next_attempt_at=NOW,
        attempt_count=0,
        error_category=None,
        error_message=None,
        completed_at=None,
    )
    for key, value in values.items():
        setattr(row, key, value)
    return row


def test_backoff_doubles_per_attempt_up_to_its_cap() -> None:
    assert [RETRIES.backoff(attempt).total_seconds() for attempt in range(0, 7)] == [
        30, 30, 60, 120, 240, 480, 900,
    ]  # fmt: skip


def test_a_retry_after_the_server_asked_for_wins_but_stays_under_the_cap() -> None:
    assert RETRIES.backoff(1, retry_after=200) == timedelta(seconds=200)
    assert RETRIES.backoff(1, retry_after=5000) == timedelta(seconds=900)
    assert RETRIES.backoff(1, retry_after=-3) == timedelta(0)


def test_only_a_due_unleased_active_row_is_claimable() -> None:
    assert claimable(_row(), NOW)
    assert claimable(_row(status="retrying", next_attempt_at=NOW - timedelta(seconds=1)), NOW)
    assert not claimable(_row(next_attempt_at=NOW + timedelta(seconds=1)), NOW)
    assert not claimable(_row(status="failed"), NOW)
    assert not claimable(_row(status="succeeded"), NOW)
    assert not claimable(_row(status="running", claim_expires_at=NOW + timedelta(1)), NOW)
    # A lease that ran out frees the row for the next worker.
    assert claimable(_row(status="running", claim_expires_at=NOW), NOW)


def test_a_claim_hands_out_a_fresh_token_and_lease_and_counts_the_attempt() -> None:
    row = _row(claim_token="old", claim_expires_at=NOW)
    token = claim(row, NOW, lease_seconds=60)
    assert token != "old" and row.claim_token == token
    assert (row.status, row.claim_expires_at, row.attempt_count) == (
        "running",
        NOW + timedelta(seconds=60),
        1,
    )
    assert claim(_row(), NOW, 60) != token
    uncounted = _row()
    claim(uncounted, NOW, 60, count_attempt=False)
    assert uncounted.attempt_count == 0


def test_only_the_current_holder_holds_the_claim() -> None:
    row = _row()
    token = claim(row, NOW, 60)
    assert holds(row, token, NOW)
    assert not holds(row, "another", NOW)
    assert not holds(row, token, NOW + timedelta(seconds=60))
    assert not holds(None, token, NOW)
    release(row)
    assert (row.claim_token, row.claim_expires_at) == (None, None)
    assert not holds(row, token, NOW)


def test_a_heartbeat_extends_the_lease_only_for_the_right_token() -> None:
    row = _row()
    token = claim(row, NOW, 60)
    later = NOW + timedelta(seconds=30)
    assert not renew(row, "another", later, 60)
    assert row.claim_expires_at == NOW + timedelta(seconds=60)
    assert renew(row, token, later, 60)
    assert row.claim_expires_at == later + timedelta(seconds=60)
    # A lease that already ran out belongs to whoever claims next; it is not revived.
    assert not renew(row, token, later + timedelta(seconds=60), 60)
    finished = _row(status="succeeded", claim_token=token, claim_expires_at=later)
    assert not renew(finished, token, NOW, 60)


def test_a_failure_is_retried_after_its_backoff() -> None:
    row = _row(status="running", claim_token="t", claim_expires_at=NOW, attempt_count=2)
    record_failure(row, NOW, "processor", "x" * 2000, RETRIES, attempts=2)
    assert row.status == "retrying"
    assert row.next_attempt_at == NOW + timedelta(seconds=60)
    assert (row.claim_token, row.claim_expires_at, row.completed_at) == (None, None, None)
    assert row.error_category == "processor" and len(row.error_message) == 1000


def test_a_failure_past_its_max_attempts_fails() -> None:
    row = _row(status="running", attempt_count=5)
    record_failure(row, NOW, "processor", "boom", RETRIES, attempts=5)
    assert (row.status, row.completed_at) == ("failed", NOW)


def test_a_permanent_failure_fails_at_once() -> None:
    row = _row(status="running", attempt_count=1)
    record_failure(row, NOW, "configuration", "boom", RETRIES, attempts=1, permanent=True)
    assert row.status == "failed"


def test_without_a_limit_a_failure_is_always_retried() -> None:
    row = SimpleNamespace(**{k: v for k, v in vars(_row()).items() if k != "completed_at"})
    record_failure(row, NOW, "search", "down", Retries(5, 3600, None), attempts=40)
    assert row.status == "retrying" and row.next_attempt_at == NOW + timedelta(seconds=3600)


def test_a_server_retry_after_sets_the_next_attempt() -> None:
    row = _row(status="running")
    record_failure(row, NOW, "http", "429", RETRIES, attempts=1, retry_after=120)
    assert row.next_attempt_at == NOW + timedelta(seconds=120)


def test_a_heartbeat_comes_three_times_per_lease_within_bounds() -> None:
    assert heartbeat_interval(60) == 20
    assert heartbeat_interval(300) == 30
    assert heartbeat_interval(1) == 1


@pytest.mark.asyncio
async def test_the_heartbeat_renews_until_the_work_finishes() -> None:
    renewals: list[None] = []

    async def renew_once() -> bool:
        renewals.append(None)
        return True

    finished = asyncio.Event()
    beating = asyncio.create_task(heartbeat(renew_once, 0.03, finished))
    await asyncio.sleep(0.1)
    finished.set()
    await asyncio.wait_for(beating, 1)
    assert len(renewals) >= 2


@pytest.mark.asyncio
async def test_the_heartbeat_stops_once_the_claim_is_lost() -> None:
    calls: list[None] = []

    async def lost() -> bool:
        calls.append(None)
        return False

    await asyncio.wait_for(heartbeat(lost, 0.03, asyncio.Event()), 1)
    assert len(calls) == 1
