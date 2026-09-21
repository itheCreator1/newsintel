import logging
import threading
import time
from typing import Any

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings
from app.operations import heartbeat

BEAT_SECONDS = 15
logger = logging.getLogger(__name__)


def beat_queues(client: Any, queues: list[str]) -> None:
    for queue in queues:
        heartbeat.beat_sync(client, f"queue:{queue}")


def _beat_forever(client: Any, queues: list[str]) -> None:
    while True:
        try:
            beat_queues(client, queues)
        except Exception:
            logger.warning("Worker heartbeat failed", exc_info=True)
        time.sleep(BEAT_SECONDS)


class QueueHeartbeat(dramatiq.Middleware):
    """Each worker process marks the queues it consumes as alive, so Operations can tell a dead
    worker from an idle one. The API and the scheduler only enqueue: they boot no worker and never
    beat (Dramatiq's own `__heartbeats__` set counts producers too)."""

    def after_worker_boot(self, broker: Any, worker: Any) -> None:
        queues = sorted(worker.consumer_whitelist or broker.get_declared_queues())
        threading.Thread(target=_beat_forever, args=(broker.client, queues), daemon=True).start()


broker = RedisBroker(url=get_settings().redis_url)  # type: ignore[no-untyped-call]
broker.add_middleware(QueueHeartbeat())
dramatiq.set_broker(broker)
