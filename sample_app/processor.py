"""Processing component with an optional, intentional failure path."""


def process_documents(documents: list[dict], *, fail: bool = False) -> list[str]:
    """Normalize retrieved titles or raise a deliberate diagnostic error."""
    if fail:
        raise ValueError("sample processor deliberately failed")
    return [document["title"].upper() for document in documents]
