import dramatiq

from app.jobs.broker import broker as broker


@dramatiq.actor(max_retries=3)
def diagnostic_ping() -> None:
    return None
