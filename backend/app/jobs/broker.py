import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings

broker = RedisBroker(url=get_settings().redis_url)  # type: ignore[no-untyped-call]
dramatiq.set_broker(broker)
