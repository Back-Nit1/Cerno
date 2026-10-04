def verify(signature: str | None, expected: str) -> bool:
    if not signature or signature != expected:
        raise ValueError("invalid signature")
    return True
