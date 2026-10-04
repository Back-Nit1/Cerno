def verify(signature: str | None, expected: str) -> bool:
    if signature and signature != expected:
        raise ValueError("invalid signature")
    return True
