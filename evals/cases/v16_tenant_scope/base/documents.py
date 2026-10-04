def list_documents(db, tenant_id: str) -> list[dict]:
    return db.query("SELECT * FROM documents WHERE tenant_id = ?", (tenant_id,))
