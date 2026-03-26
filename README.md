# 💎 Vibe Debug: AI Pipeline Observability Toolkit 

Vibe Debug is a local-first, universal OTel-based toolkit for debugging AI pipelines. It captures traces and surfaces them to IDEs via the Model Context Protocol (MCP).

---

## 🚀 Get Started

### 1. 🏗️ Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Create .env from example (default PHOENIX_PORT=6006)
cp .env.example .env
```

### 2. 🖥️ Launch Phoenix (Observability UI)
Vibe Debug uses [Arize Phoenix](https://phoenix.arize.com/) as its backend. Start it with:
```bash
python -m server.manager
```
Once started, view the dashboard at [http://localhost:6006](http://localhost:6006).

### 3. 🛸 Integrate into Your Project
Add just 2 lines to any Python function to start tracing:

```python
from vibe_debug import init_vibedebug, vibetrace

# Initialize (must call once per script)
init_vibedebug(project_name="My-AI-App")

# Decorate any function
@vibetrace(component_type="llm")
def call_llm(prompt):
    return "The answer is..."
```

**What Vibe Debug captures:**
- **Inputs & Outputs:** Automatically stringified.
- **Timing:** Accurate execution time in ms.
- **Silent Failure Detection:** 🧩 Automatically flags `None`, `[]`, or `""` as **ERROR** in Phoenix.
- **Exceptions:** Re-records full stack traces.

---

## 🌉 MCP Bridge (IDE Integration)
Vibe Debug includes an MCP server that allows Claude Desktop, Cursor, and other AI editors to directly query your Phoenix instance for errors.

**Bridge Tools:**
- `get_last_failure`: Finds the most recent span with an "ERROR" status (including silent failures).
- `explain_trace(trace_id)`: Generates a human-readable "waterfall" summary for the AI assistant to analyze.

---

## 🧪 Testing

### 🌊 Run Integration Test
This simulates a 3-step AI pipeline (Router -> Retrieval -> LLM) where the Retrieval step fails silently (returns an empty list).
```bash
python -m tests.mock_pipeline
```
*Wait 5 seconds for OTel batch exporter, then refresh Phoenix UI.*

### ✅ Run Unit Tests
```bash
pytest tests/test_instrument.py
```

---

## 📁 Architecture
- `vibe_debug/`: Core SDK logic (initializers, OTel exporters, decorators).
- `server/`: Phoenix launcher and FastMCP bridge server.
- `tests/`: Integration and unit test suites.
- `.env`: Port/Endpoint configuration.
