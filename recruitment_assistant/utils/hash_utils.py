from hashlib import sha256


def text_hash(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return sha256(normalized.encode("utf-8")).hexdigest()


def mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 7:
        return value
    return f"{digits[:3]}****{digits[-4:]}"


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return value
    name, domain = value.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*" if name else "*"
    else:
        masked = f"{name[0]}***{name[-1]}"
    return f"{masked}@{domain}"
