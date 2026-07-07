# Experiments

Self-contained workbench for ideas that use project tools without touching the main loop.

## Active

```text
tweet_explorer/
```

Explores budgeted X Post investigations from one starting Post ID.

## Folder Contract

Each experiment owns its local scripts, generated runs, memory, and cleanup rules.
Do not import experiment code from production scripts.

```text
experiments/
  YYYY-MM-DD_short-name/
    README.md
    scripts/
      playbook.py
      probe.py
```

Generated run files should be written under a local `runs/` directory created by the experiment at runtime.
Promote useful code into `scripts/` only after the experiment proves itself.
