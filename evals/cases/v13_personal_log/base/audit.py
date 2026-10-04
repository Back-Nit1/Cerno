def record_login(logger, user: dict) -> None:
    logger.info("login user_id=%s", user["id"])
