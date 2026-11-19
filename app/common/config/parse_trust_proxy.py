def parse_trust_proxy(raw: str | None) -> bool | list[str]:
    """Parse TRUST_PROXY into bool or list of trusted IP/CIDR strings."""
    if raw is None or raw.strip() == "":
        return False

    value = raw.strip()
    lower = value.lower()
    if lower in ("false", "0"):
        return False
    if lower in ("true", "1"):
        return True

    return [part.strip() for part in value.split(",") if part.strip()]
