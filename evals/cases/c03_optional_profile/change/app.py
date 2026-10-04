def public_profile(user: dict) -> dict:
    profile = {"id": user["id"], "name": user["name"]}
    if user.get("avatar_url"):
        profile["avatar_url"] = user["avatar_url"]
    return profile
