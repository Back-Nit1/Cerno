DEFAULT_CACHE_TTL_SECONDS = 300


def expires_at(now: int) -> int:
    return now + DEFAULT_CACHE_TTL_SECONDS
