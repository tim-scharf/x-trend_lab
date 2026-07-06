# X API Artifact Bundle

Generated from official X documentation URLs for LLM planning and spend-aware API choices.

Primary files:

- `manifest.json`: source URL, purpose, fetch status, and local filename.
- `x_api_cost_profile.json`: compact spend/rate-limit summary extracted from downloaded docs.
- `sources/`: raw official Markdown, `llms.txt`, and OpenAPI source files.

Refresh with:

```bash
python scripts/refresh_x_api_artifacts.py
```
