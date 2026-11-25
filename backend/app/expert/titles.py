"""Safe normalization for provider-generated conversation topic titles."""


def generated_conversation_title(value: object) -> str | None:
    """Return a concise printable title, discarding malformed values wholesale."""
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    title = " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )
    if not title or len(title) > 80:
        return None
    return title
