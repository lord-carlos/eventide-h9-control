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
        self._channel_left_combo: QtWidgets.QComboBox | None = None
        self._channel_right_combo: QtWidgets.QComboBox | None = None

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

        # Channel Selection - Left Channel
        self._channel_left_combo = QtWidgets.QComboBox()
        self._channel_left_combo.setMinimumWidth(300)
        self._channel_left_combo.currentIndexChanged.connect(self._on_channel_changed)
        
        lbl_channel_left = QtWidgets.QLabel("Left Channel:")
        lbl_channel_left.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_channel_left, self._channel_left_combo)

        # Channel Selection - Right Channel
        self._channel_right_combo = QtWidgets.QComboBox()
        self._channel_right_combo.setMinimumWidth(300)
        self._channel_right_combo.currentIndexChanged.connect(self._on_channel_changed)
        
        lbl_channel_right = QtWidgets.QLabel("Right Channel:")
        lbl_channel_right.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_channel_right, self._channel_right_combo)

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

    def _populate_channels(self, device_id: int | None) -> None:
        """Populate channel combo boxes based on selected device's capabilities."""
        if self._channel_left_combo is None or self._channel_right_combo is None:
            return
            
        self._channel_left_combo.clear()
        self._channel_right_combo.clear()
        
        if device_id is None:
            return
            
        try:
            info = self._pyaudio.get_device_info_by_index(device_id)
            max_channels = int(info.get("maxInputChannels", 2))
            
            for i in range(max_channels):
                self._channel_left_combo.addItem(f"Channel {i}", userData=i)
                self._channel_right_combo.addItem(f"Channel {i}", userData=i)
                
        except Exception as e:
            logging.error(f"Error getting device info: {e}")

    def _load_settings(self) -> None:
        # Audio Device
        current_device_id = self.config.audio_input_device_id
        if current_device_id is not None:
            index = self._device_combo.findData(current_device_id)
            if index >= 0:
                self._device_combo.setCurrentIndex(index)
                # Populate channels for the current device
                self._populate_channels(current_device_id)
        
        # Load selected channels
        selected_channels = self.config.audio_selected_channels
        if len(selected_channels) >= 2:
            # Set left channel
            left_idx = self._channel_left_combo.findData(selected_channels[0]) if self._channel_left_combo else -1
            if left_idx >= 0:
                self._channel_left_combo.setCurrentIndex(left_idx)
            
            # Set right channel
            right_idx = self._channel_right_combo.findData(selected_channels[1]) if self._channel_right_combo else -1
            if right_idx >= 0:
                self._channel_right_combo.setCurrentIndex(right_idx)
        
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
            
            # Populate channels for the new device
            self._populate_channels(device_id)
            
            # Reset to default channels [0, 1] when device changes
            if self._channel_left_combo and self._channel_left_combo.count() > 0:
                self._channel_left_combo.setCurrentIndex(0)
            if self._channel_right_combo and self._channel_right_combo.count() > 1:
                self._channel_right_combo.setCurrentIndex(1)
            
            # Save default channels
            self.config.audio_selected_channels = [0, 1]
            
            # Note: Changing device might require restarting the BeatDetector.
            # We should probably signal this change or let the main app handle it.
            # For now, we just save config. Ideally, we'd emit a signal "settings_changed".

    def _on_bpm_mode_changed(self, button: QtWidgets.QAbstractButton) -> None:
        if button == self._bpm_mode_continuous:
            self.config.auto_bpm_mode = "continuous"
        else:
            self.config.auto_bpm_mode = "manual"
        logging.info(f"BPM mode changed to: {self.config.auto_bpm_mode}")

    def _on_channel_changed(self) -> None:
        """Save selected channels when either channel combo changes."""
        if self._channel_left_combo is None or self._channel_right_combo is None:
            return
            
        left_channel = self._channel_left_combo.currentData()
        right_channel = self._channel_right_combo.currentData()
        
        if left_channel is not None and right_channel is not None:
            # Validate against device capabilities
            device_id = self.config.audio_input_device_id
            if device_id is not None:
                try:
                    info = self._pyaudio.get_device_info_by_index(device_id)
                    max_channels = int(info.get("maxInputChannels", 2))
                    
                    # If selected channels exceed device capabilities, reset to [0, 1]
                    if left_channel >= max_channels or right_channel >= max_channels:
                        logging.warning(f"Selected channels [{left_channel}, {right_channel}] exceed device max {max_channels}, resetting to [0, 1]")
                        left_channel = 0
                        right_channel = 1
                        self._channel_left_combo.setCurrentIndex(0)
                        if self._channel_right_combo.count() > 1:
                            self._channel_right_combo.setCurrentIndex(1)
                except Exception as e:
                    logging.error(f"Error validating channels: {e}")
                    left_channel = 0
                    right_channel = 1
            
            self.config.audio_selected_channels = [left_channel, right_channel]
            logging.info(f"Selected channels: {[left_channel, right_channel]}")

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
