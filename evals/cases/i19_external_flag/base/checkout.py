def checkout_mode(flags: dict[str, str]) -> str:
    return flags.get("checkout_mode", "legacy")
