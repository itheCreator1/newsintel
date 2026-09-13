from app.jobs.diagnostics import diagnostic_ping


def test_diagnostic_actor_completes_without_unused_result() -> None:
    assert diagnostic_ping.fn() is None
