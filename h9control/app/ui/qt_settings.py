from __future__ import annotations

import logging
from typing import Any

import pyaudio
from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.config import ConfigManager


class SettingsWidget(QtWidgets.QWidget):
    back_requested = QtCore.Signal()
    settings_changed = QtCore.Signal()  # Emitted when settings change that affect UI

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self._pyaudio = pyaudio.PyAudio()

        self._init_ui()
        self._load_settings()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        # Title
        title = QtWidgets.QLabel("Settings")
        font = QtGui.QFont()
        font.setPointSize(24)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Form Layout
        form_layout = QtWidgets.QFormLayout()
        form_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_layout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop)
        form_layout.setSpacing(15)

        # TODO: Add knob_order to settings UI (configurable list of which knobs to display and in what order)

        # Audio Device
        self._device_combo = QtWidgets.QComboBox()
        self._device_combo.setMinimumWidth(300)
        self._populate_devices()
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        
        lbl_device = QtWidgets.QLabel("Audio Input Device:")
        lbl_device.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_device, self._device_combo)

        # Auto BPM Send
        self._bpm_mode_group = QtWidgets.QButtonGroup(self)
        self._bpm_mode_manual = QtWidgets.QRadioButton("Manual (Current)")
        self._bpm_mode_continuous = QtWidgets.QRadioButton("Continuous")
        
        self._bpm_mode_manual.setFont(QtGui.QFont("Arial", 12))
        self._bpm_mode_continuous.setFont(QtGui.QFont("Arial", 12))

        self._bpm_mode_group.addButton(self._bpm_mode_manual)
        self._bpm_mode_group.addButton(self._bpm_mode_continuous)
        
        self._bpm_mode_group.buttonClicked.connect(self._on_bpm_mode_changed)

        bpm_layout = QtWidgets.QHBoxLayout()
        bpm_layout.addWidget(self._bpm_mode_manual)
        bpm_layout.addWidget(self._bpm_mode_continuous)
        bpm_layout.addStretch()

        lbl_bpm = QtWidgets.QLabel("Auto BPM Send:")
        lbl_bpm.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_bpm, bpm_layout)

        # Lock Delay Checkbox
        self._lock_delay_checkbox = QtWidgets.QCheckBox("Lock Delay A/B Together")
        self._lock_delay_checkbox.setFont(QtGui.QFont("Arial", 12))
        self._lock_delay_checkbox.stateChanged.connect(self._on_lock_delay_changed)
        
        lbl_lock_delay = QtWidgets.QLabel("Delay Lock:")
        lbl_lock_delay.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_delay, self._lock_delay_checkbox)

        # Lock Feedback Checkbox
        self._lock_feedback_checkbox = QtWidgets.QCheckBox("Lock Feedback A/B Together")
        self._lock_feedback_checkbox.setFont(QtGui.QFont("Arial", 12))
        self._lock_feedback_checkbox.stateChanged.connect(self._on_lock_feedback_changed)
        
        lbl_lock_feedback = QtWidgets.QLabel("Feedback Lock:")
        lbl_lock_feedback.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_feedback, self._lock_feedback_checkbox)

        # Lock Pitch Checkbox
        self._lock_pitch_checkbox = QtWidgets.QCheckBox("Lock Pitch A/B Together")
        self._lock_pitch_checkbox.setFont(QtGui.QFont("Arial", 12))
        self._lock_pitch_checkbox.stateChanged.connect(self._on_lock_pitch_changed)
        
        lbl_lock_pitch = QtWidgets.QLabel("Pitch Lock:")
        lbl_lock_pitch.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_pitch, self._lock_pitch_checkbox)

        layout.addLayout(form_layout)
        layout.addStretch()

        # Back Button
        self._btn_back = QtWidgets.QPushButton("Back")
        self._btn_back.setMinimumHeight(50)
        self._btn_back.setFont(QtGui.QFont("Arial", 16))
        self._btn_back.clicked.connect(self.back_requested.emit)
        layout.addWidget(self._btn_back)

    def _populate_devices(self) -> None:
        self._device_combo.clear()
        
        # Add "Default" option? Or just list devices.
        # Let's list devices.
        
        try:
            count = self._pyaudio.get_device_count()
            for i in range(count):
                info = self._pyaudio.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    name = info.get("name", f"Device {i}")
                    self._device_combo.addItem(name, userData=i)
        except Exception as e:
            logging.error(f"Error listing audio devices: {e}")

    def _load_settings(self) -> None:
        # Audio Device
        current_device_id = self.config.audio_input_device_id
        if current_device_id is not None:
            index = self._device_combo.findData(current_device_id)
            if index >= 0:
                self._device_combo.setCurrentIndex(index)
        
        # BPM Mode
        mode = self.config.auto_bpm_mode
        if mode == "continuous":
            self._bpm_mode_continuous.setChecked(True)
        else:
            self._bpm_mode_manual.setChecked(True)
        
        # Lock settings
        self._lock_delay_checkbox.setChecked(self.config.lock_delay)
        self._lock_feedback_checkbox.setChecked(self.config.lock_feedback)
        self._lock_pitch_checkbox.setChecked(self.config.lock_pitch)

    def _on_device_changed(self, index: int) -> None:
        device_id = self._device_combo.itemData(index)
        if device_id is not None:
            self.config.audio_input_device_id = int(device_id)
            logging.info(f"Selected audio device: {device_id}")
            # Note: Changing device might require restarting the BeatDetector.
            # We should probably signal this change or let the main app handle it.
            # For now, we just save config. Ideally, we'd emit a signal "settings_changed".

    def _on_bpm_mode_changed(self, button: QtWidgets.QAbstractButton) -> None:
        if button == self._bpm_mode_continuous:
            self.config.auto_bpm_mode = "continuous"
        else:
            self.config.auto_bpm_mode = "manual"
        logging.info(f"BPM mode changed to: {self.config.auto_bpm_mode}")

    def _on_lock_delay_changed(self, state: int) -> None:
        self.config.lock_delay = (state == QtCore.Qt.CheckState.Checked.value)
        logging.info(f"Lock delay changed to: {self.config.lock_delay}")
        self.settings_changed.emit()

    def _on_lock_feedback_changed(self, state: int) -> None:
        self.config.lock_feedback = (state == QtCore.Qt.CheckState.Checked.value)
        logging.info(f"Lock feedback changed to: {self.config.lock_feedback}")
        self.settings_changed.emit()

    def _on_lock_pitch_changed(self, state: int) -> None:
        self.config.lock_pitch = (state == QtCore.Qt.CheckState.Checked.value)
        logging.info(f"Lock pitch changed to: {self.config.lock_pitch}")
        self.settings_changed.emit()

    def __del__(self) -> None:
        self._pyaudio.terminate()
