# tests/mock_pipeline.py
# NOTE: No imports of vibetrace here - this is intentional.
# Auto-instrumentation patches these functions from outside.

def route_query(query: str) -> str:
    """Routes incoming query to the appropriate handler."""
    if "graph" in query.lower():
        return "knowledge_graph"
    return "rag"


def retrieve_documents(query: str) -> list:
    """Retrieves relevant documents from vector store."""
    if not query:
        return []
    return [
        {"id": 1, "text": "Document about transformers"},
        {"id": 2, "text": "Document about attention"},
    ]


def lookup_knowledge_graph(entities: list) -> dict:
    """Looks up entity relationships in Neo4j knowledge graph."""
    if not entities:
        return {}
    return {"transformer": ["attention", "encoder", "decoder"]}


def generate_response(context: list, query: str) -> str:
    """Generates final response using LLM with retrieved context."""
    if not context:
        return "I could not find relevant information."
    return f"Based on {len(context)} documents: answer to '{query}'"
