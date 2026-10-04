def audit_login(logger, user: dict) -> None:
    redacted_id = f"user-{user['id'] % 1000:03d}"
    logger.info("login principal=%s", redacted_id)
