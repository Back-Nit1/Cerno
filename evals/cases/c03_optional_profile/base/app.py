def public_profile(user: dict) -> dict:
    return {"id": user["id"], "name": user["name"]}
