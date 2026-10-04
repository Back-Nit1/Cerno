def checkout_mode(flags: dict[str, str]) -> str:
    return flags.get("checkout_v2_mode", "legacy")
