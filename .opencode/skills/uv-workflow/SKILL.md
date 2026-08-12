---
name: uv-workflow
description: Use when adding, removing, or managing Python dependencies, setting up environments, or running this app. Covers the UV-first tooling workflow for this project.
---

# UV workflow

- Use UV over other environment managers.
- Use `uv add <dependency>` over `pip install` where possible.
- Use `pyproject.toml` for dependencies where possible.
- Don't use `requirements.txt`.
- Run `uv sync` after adding dependencies.

# Compile and run

- Run the app with `uv run python ui_main.py --log-level DEBUG`. Make sure to kill it before starting a second instance.
