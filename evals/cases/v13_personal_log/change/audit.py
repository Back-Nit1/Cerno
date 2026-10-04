def record_login(logger, user: dict) -> None:
    logger.info("login contact=%s", user["contact"])
