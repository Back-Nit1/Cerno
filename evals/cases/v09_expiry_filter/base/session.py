def active_sessions(rows: list[dict], now: int) -> list[dict]:
    return [row for row in rows if row["expires_at"] > now]
