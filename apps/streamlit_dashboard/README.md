# X Trend Lab Dashboard

Local Streamlit dashboard for inspecting the X Trend Lab pipeline artifacts.

Run from the project root:

```bash
streamlit run apps/streamlit_dashboard/app.py
```

The dashboard reads existing local files only:

- `data/runtime/x_trends.db`
- `data/runtime/query_level_history.csv`
- `data/generated/generated_queries_*.json`
- `data/snapshots/batch_*.json`
- `data/reasoning/reasoning_*.json`

