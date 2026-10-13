"""Row ids: UUIDv7 (RFC 9562), a millisecond timestamp followed by random bits.

Ids made later sort later, so a big table's primary-key index grows at its end instead of
splitting pages at random as uuid4 keys do. Within one millisecond the random part counts up by
one (the RFC's "monotonic random" method), so ids from this process are strictly increasing even
if the clock steps back. Claim tokens and other secrets stay uuid4: these ids are guessable.
"""

import os
import threading
import time
import uuid

_RANDOM_BITS = 74  # rand_a (12 bits) and rand_b (62 bits)
_lock = threading.Lock()
_last_ms = 0
_last_random = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _last_random
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            # Start low in the range, so a busy millisecond has room to count up.
            _last_random = int.from_bytes(os.urandom(10)) >> (80 - _RANDOM_BITS + 1)
        else:
            _last_random += 1
            if _last_random >> _RANDOM_BITS:
                _last_ms += 1
                _last_random = int.from_bytes(os.urandom(10)) >> (80 - _RANDOM_BITS + 1)
        ms, random = _last_ms, _last_random
    rand_a = random >> 62
    rand_b = random & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)
