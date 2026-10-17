from app.jobs.diagnostics import diagnostic_ping


def test_diagnostic_actor_completes_without_unused_result() -> None:
    assert diagnostic_ping.fn() is None


def test_every_actor_leaves_retries_to_the_lease() -> None:
    # A claimed row is retried by its lease and its own backoff. Dramatiq's retries would send the
    # same claim again, past the row's attempt limit and outside its backoff (fix plan Δ2β).
    import importlib
    import pkgutil

    import app.jobs
    from app.jobs.broker import broker

    for module in pkgutil.iter_modules(app.jobs.__path__):
        importlib.import_module(f"app.jobs.{module.name}")
    actors = {name: broker.get_actor(name) for name in broker.get_declared_actors()}
    assert {"index_article", "process_nlp", "ingest_feed"} <= set(actors)
    assert {name: actor.options.get("max_retries") for name, actor in actors.items()} == {
        name: 0 for name in actors
    }
