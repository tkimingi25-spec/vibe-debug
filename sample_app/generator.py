"""Response-generation component used by the live demonstration."""


def generate_response(processed_documents: list[str], query: str) -> str:
    """Produce a small response from the processed document titles."""
    return f"Answer for {query}: {', '.join(processed_documents)}"
