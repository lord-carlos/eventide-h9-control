---
applyTo: "**"
---

# Tooling

 - use UV over other envonment managers
 - use uv add <dependency> .. over pip install .. where possible.
 - use pyproject.toml for dependencies where possible
 - don't use requirements.txt
 - run `uv sync` after adding dependencies.

 # compile and run

 - Run the app with `uv run python ui_main.py --log-level DEBUG` make sure to kill it before starting a second instant.