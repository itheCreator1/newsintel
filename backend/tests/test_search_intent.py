import uuid
from datetime import UTC, datetime, timedelta

from app.search.service import next_retry_at


def test_index_retry_uses_capped_exponential_backoff() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)

    assert next_retry_at(now, 1) == now + timedelta(seconds=5)
    assert next_retry_at(now, 4) == now + timedelta(seconds=40)
    assert next_retry_at(now, 20) == now + timedelta(hours=1)


def test_index_claim_tokens_are_unique() -> None:
    from app.search.service import new_claim_token

    assert new_claim_token() != new_claim_token()
    assert len(new_claim_token()) == len(uuid.uuid4().hex)
