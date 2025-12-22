from __future__ import annotations

import logging
import os


def configure_logging(*, cli_level: str | None = None) -> None:
    """Configure root logging for the app.

    Precedence:
    1) `cli_level` (e.g. from argparse)
    2) env var `H9_LOG_LEVEL`
    3) default INFO

    This should be called once, early in the entrypoint.
    """

    level_name = (cli_level or os.environ.get("H9_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
