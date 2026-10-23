"""Deterministic retrieval component used by the live demonstration."""


def retrieve_documents(query: str) -> list[dict]:
    """Return sample documents that match a user query."""
    return [
        {"id": "doc-1", "title": "Tracing pipelines", "query": query},
        {"id": "doc-2", "title": "Debugging failures", "query": query},
    ]
