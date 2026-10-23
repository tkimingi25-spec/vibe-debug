# vibe-debug sample application

This is a complete four-module pipeline: `main.py` calls `retriever.py`,
`processor.py`, and `generator.py`. The `--fail` option creates a deliberate
`ValueError` in the processor so the Phoenix UI and MCP failure tools have a
real failure to inspect.

Run it from this directory after starting Phoenix:

```powershell
python main.py
python main.py --fail
```

The single integration line is in `main.py`:

```python
vibe_debug.start("vibe-debug-sample", project_root=str(root))
```
