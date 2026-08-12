from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6 import QtCore

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState
from h9control.app.ui.qt_worker import H9DeviceWorker


@pytest.fixture(scope="session")
def qapp() -> QtCore.QCoreApplication:
    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtCore.QCoreApplication([])
    return app


@pytest.fixture()
def config(tmp_path: Path) -> ConfigManager:
    return ConfigManager(tmp_path / "config.json")


@pytest.fixture()
def sim_worker(config: ConfigManager, qapp: QtCore.QCoreApplication) -> H9DeviceWorker:
    worker = H9DeviceWorker(config=config, simulate_h9=True)
    yield worker
    worker.shutdown()


@pytest.fixture()
def worker_states(sim_worker: H9DeviceWorker) -> list[DashboardState]:
    states: list[DashboardState] = []
    sim_worker.state_changed.connect(states.append)
    return states
