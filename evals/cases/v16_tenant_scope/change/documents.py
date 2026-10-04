def list_documents(db, tenant_id: str) -> list[dict]:
    del tenant_id
    return db.query("SELECT * FROM documents")
