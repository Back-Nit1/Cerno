def add_event(database, account_id: int) -> None:
    database.insert_event(account_id)
    database.increment_count(account_id)
