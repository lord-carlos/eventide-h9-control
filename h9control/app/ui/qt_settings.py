from __future__ import annotations

import logging
from typing import Any

import sounddevice as sd
from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.config import ConfigManager
from h9control.hardware.backlight import BacklightController

# Touch-friendly sizing constants
COMBOBOX_MIN_HEIGHT = 50  # Minimum height for dropdown selectors
COMBOBOX_MIN_WIDTH = 400  # Minimum width for better touch targets
CHECKBOX_MIN_HEIGHT = 44  # Minimum height for checkbox containers
RADIO_BUTTON_MIN_HEIGHT = 44  # Minimum height for radio button containers
TOUCH_SPACING = 24  # Spacing between form rows for touch accuracy
CONTROL_FONT_SIZE = 14  # Font size for interactive controls (increased from 12)

# Stylesheet for larger checkbox and radio button indicators
CHECKBOX_STYLESHEET = """
    QCheckBox::indicator {
        width: 24px;
        height: 24px;
    }
"""

RADIO_BUTTON_STYLESHEET = """
    QRadioButton::indicator {
        width: 24px;
        height: 24px;
    }
"""


def configure_combobox_for_touch(combo: QtWidgets.QComboBox) -> None:
    """Configure a combo box for touch-friendly dropdown interaction."""
    # Larger arrow button
    combo.setStyleSheet("""
        QComboBox::drop-down {
            width: 40px;
        }
    """)

    # Set larger font for dropdown items - this works across all platforms
    view_font = QtGui.QFont("Arial", 24)
    combo.setFont(view_font)

    # Configure the popup view for larger items
    view = combo.view()
    if view:
        view.setFont(view_font)
        # Set minimum row height for touch targets
        view.setMinimumHeight(300)  # Make popup taller overall


class SettingsWidget(QtWidgets.QWidget):
    back_requested = QtCore.Signal()
    settings_changed = QtCore.Signal()  # Emitted when settings change that affect UI
    audio_settings_changed = (
        QtCore.Signal()
    )  # Emitted when audio device/channel settings change

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self._channel_left_combo: QtWidgets.QComboBox | None = None
        self._channel_right_combo: QtWidgets.QComboBox | None = None
        self._brightness_slider: QtWidgets.QSlider | None = None
        self._backlight = BacklightController()

        self._init_ui()
        self._load_settings()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Title
        title = QtWidgets.QLabel("Settings")
        font = QtGui.QFont()
        font.setPointSize(24)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Scroll area for form content
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
            }
            QScrollBar:vertical {
                width: 24px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #888888;
                border-radius: 12px;
                min-height: 40px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        # Form widget and layout inside scroll area
        form_widget = QtWidgets.QWidget()
        form_layout = QtWidgets.QFormLayout(form_widget)
        form_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_layout.setFormAlignment(
            QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop
        )
        form_layout.setSpacing(TOUCH_SPACING)

        # TODO: Add knob_order to settings UI (configurable list of which knobs to display and in what order)

        # Audio Device
        self._device_combo = QtWidgets.QComboBox()
        self._device_combo.setMinimumWidth(COMBOBOX_MIN_WIDTH)
        self._device_combo.setMinimumHeight(COMBOBOX_MIN_HEIGHT)
        configure_combobox_for_touch(self._device_combo)
        self._populate_devices()
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)

        lbl_device = QtWidgets.QLabel("Audio Input Device:")
        lbl_device.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_device, self._device_combo)

        # Channel Selection - Left Channel
        self._channel_left_combo = QtWidgets.QComboBox()
        self._channel_left_combo.setMinimumWidth(COMBOBOX_MIN_WIDTH)
        self._channel_left_combo.setMinimumHeight(COMBOBOX_MIN_HEIGHT)
        configure_combobox_for_touch(self._channel_left_combo)
        self._channel_left_combo.currentIndexChanged.connect(self._on_channel_changed)

        lbl_channel_left = QtWidgets.QLabel("Left Channel:")
        lbl_channel_left.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_channel_left, self._channel_left_combo)

        # Channel Selection - Right Channel
        self._channel_right_combo = QtWidgets.QComboBox()
        self._channel_right_combo.setMinimumWidth(COMBOBOX_MIN_WIDTH)
        self._channel_right_combo.setMinimumHeight(COMBOBOX_MIN_HEIGHT)
        configure_combobox_for_touch(self._channel_right_combo)
        self._channel_right_combo.currentIndexChanged.connect(self._on_channel_changed)

        lbl_channel_right = QtWidgets.QLabel("Right Channel:")
        lbl_channel_right.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_channel_right, self._channel_right_combo)

        # Auto BPM Send
        self._bpm_mode_group = QtWidgets.QButtonGroup(self)
        self._bpm_mode_manual = QtWidgets.QRadioButton("Manual (Current)")
        self._bpm_mode_continuous = QtWidgets.QRadioButton("Continuous")

        self._bpm_mode_manual.setFont(QtGui.QFont("Arial", CONTROL_FONT_SIZE))
        self._bpm_mode_continuous.setFont(QtGui.QFont("Arial", CONTROL_FONT_SIZE))
        self._bpm_mode_manual.setMinimumHeight(RADIO_BUTTON_MIN_HEIGHT)
        self._bpm_mode_continuous.setMinimumHeight(RADIO_BUTTON_MIN_HEIGHT)
        self._bpm_mode_manual.setStyleSheet(RADIO_BUTTON_STYLESHEET)
        self._bpm_mode_continuous.setStyleSheet(RADIO_BUTTON_STYLESHEET)

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
        self._lock_delay_checkbox.setFont(QtGui.QFont("Arial", CONTROL_FONT_SIZE))
        self._lock_delay_checkbox.setMinimumHeight(CHECKBOX_MIN_HEIGHT)
        self._lock_delay_checkbox.setStyleSheet(CHECKBOX_STYLESHEET)
        self._lock_delay_checkbox.stateChanged.connect(self._on_lock_delay_changed)

        lbl_lock_delay = QtWidgets.QLabel("Delay Lock:")
        lbl_lock_delay.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_delay, self._lock_delay_checkbox)

        # Lock Feedback Checkbox
        self._lock_feedback_checkbox = QtWidgets.QCheckBox("Lock Feedback A/B Together")
        self._lock_feedback_checkbox.setFont(QtGui.QFont("Arial", CONTROL_FONT_SIZE))
        self._lock_feedback_checkbox.setMinimumHeight(CHECKBOX_MIN_HEIGHT)
        self._lock_feedback_checkbox.setStyleSheet(CHECKBOX_STYLESHEET)
        self._lock_feedback_checkbox.stateChanged.connect(
            self._on_lock_feedback_changed
        )

        lbl_lock_feedback = QtWidgets.QLabel("Feedback Lock:")
        lbl_lock_feedback.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_feedback, self._lock_feedback_checkbox)

        # Lock Pitch Checkbox
        self._lock_pitch_checkbox = QtWidgets.QCheckBox("Lock Pitch A/B Together")
        self._lock_pitch_checkbox.setFont(QtGui.QFont("Arial", CONTROL_FONT_SIZE))
        self._lock_pitch_checkbox.setMinimumHeight(CHECKBOX_MIN_HEIGHT)
        self._lock_pitch_checkbox.setStyleSheet(CHECKBOX_STYLESHEET)
        self._lock_pitch_checkbox.stateChanged.connect(self._on_lock_pitch_changed)

        lbl_lock_pitch = QtWidgets.QLabel("Pitch Lock:")
        lbl_lock_pitch.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_lock_pitch, self._lock_pitch_checkbox)

        # Theme Selection
        self._theme_combo = QtWidgets.QComboBox()
        self._theme_combo.setMinimumWidth(COMBOBOX_MIN_WIDTH)
        self._theme_combo.setMinimumHeight(COMBOBOX_MIN_HEIGHT)
        configure_combobox_for_touch(self._theme_combo)
        self._theme_combo.addItem("System", userData="system")
        self._theme_combo.addItem("Light", userData="light")
        self._theme_combo.addItem("Dark", userData="dark")
        self._theme_combo.addItem("Darker", userData="darker")
        self._theme_combo.addItem("Crazy", userData="crazy")
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        lbl_theme = QtWidgets.QLabel("Theme:")
        lbl_theme.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_theme, self._theme_combo)

        # Display Brightness Slider
        self._brightness_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._brightness_slider.setRange(10, 100)
        self._brightness_slider.setMinimumWidth(500)
        self._brightness_slider.setMinimumHeight(60)
        self._brightness_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #999999;
                height: 20px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #333333, stop:1 #888888);
                border-radius: 10px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 2px solid #666666;
                width: 40px;
                height: 50px;
                margin: -15px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #0078d4;
                border-radius: 10px;
            }
        """)
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)

        lbl_brightness = QtWidgets.QLabel("Brightness:")
        lbl_brightness.setFont(QtGui.QFont("Arial", 14))
        form_layout.addRow(lbl_brightness, self._brightness_slider)

        # Add the form widget to scroll area
        scroll_area.setWidget(form_widget)
        layout.addWidget(scroll_area, stretch=1)

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
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                max_channels = int(dev.get("max_input_channels", 0))
                if max_channels > 0:
                    name = dev.get("name", f"Device {i}")
                    self._device_combo.addItem(name, userData=i)
        except Exception as e:
            logging.error(f"Error listing audio devices: {e}")

    def _populate_channels(self, device_id: int | None) -> None:
        """Populate channel combo boxes based on selected device's capabilities."""
        if self._channel_left_combo is None or self._channel_right_combo is None:
            return

        # Block signals to prevent triggering _on_channel_changed during population
        self._channel_left_combo.blockSignals(True)
        self._channel_right_combo.blockSignals(True)

        try:
            self._channel_left_combo.clear()
            self._channel_right_combo.clear()

            if device_id is None:
                return

            try:
                info = sd.query_devices(device_id)
                max_channels = int(info.get("max_input_channels", 2))

                for i in range(max_channels):
                    self._channel_left_combo.addItem(f"Channel {i}", userData=i)
                    self._channel_right_combo.addItem(f"Channel {i}", userData=i)

            except Exception as e:
                logging.error(f"Error getting device info: {e}")
        finally:
            # Always re-enable signals
            self._channel_left_combo.blockSignals(False)
            self._channel_right_combo.blockSignals(False)

    def _load_settings(self) -> None:
        # Audio Device - restore saved device if it exists
        current_device_id = self.config.audio_input_device_id
        if current_device_id is not None:
            index = self._device_combo.findData(current_device_id)
            if index >= 0:
                self._device_combo.setCurrentIndex(index)
            else:
                # Saved device not found - update config to fallback device
                fallback_device_id = self._device_combo.itemData(0)
                if fallback_device_id is not None:
                    logging.warning(
                        f"Saved device {current_device_id} not found, "
                        f"falling back to device {fallback_device_id}"
                    )
                    self.config.audio_input_device_id = fallback_device_id
                    # Reset channels to defaults since device changed
                    self.config.audio_selected_channels = [0, 1]

        # Always populate channels based on currently selected device
        # (handles first run, missing saved device, or device at index 0)
        selected_device_id = self._device_combo.currentData()
        if selected_device_id is not None:
            self._populate_channels(selected_device_id)

        # Load selected channels after population ensures items exist
        selected_channels = self.config.audio_selected_channels
        if len(selected_channels) >= 2:
            # Set left channel
            left_idx = (
                self._channel_left_combo.findData(selected_channels[0])
                if self._channel_left_combo
                else -1
            )
            if left_idx >= 0:
                self._channel_left_combo.setCurrentIndex(left_idx)

            # Set right channel
            right_idx = (
                self._channel_right_combo.findData(selected_channels[1])
                if self._channel_right_combo
                else -1
            )
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

        # Theme
        theme_mode = self.config.theme_mode
        theme_idx = self._theme_combo.findData(theme_mode)
        if theme_idx >= 0:
            self._theme_combo.setCurrentIndex(theme_idx)

        # Brightness - read from hardware and gray out if not available
        if self._brightness_slider:
            if self._backlight.is_available():
                current = self._backlight.get_brightness_percent()
                if current is not None:
                    self._brightness_slider.setValue(current)
                self._brightness_slider.setEnabled(True)
            else:
                self._brightness_slider.setEnabled(False)
                # Find the label for brightness and gray it out
                # The label is at index -2 in the form layout (row before last)
                # We can't easily get it, so we just disable the slider

    def _on_device_changed(self, index: int) -> None:
        device_id = self._device_combo.itemData(index)
        if device_id is not None:
            self.config.audio_input_device_id = int(device_id)
            logging.info(f"Selected audio device: {device_id}")

            # Populate channels for the new device (signals already blocked inside)
            self._populate_channels(device_id)

            # Block signals while setting default channels to prevent duplicate saves
            if self._channel_left_combo:
                self._channel_left_combo.blockSignals(True)
            if self._channel_right_combo:
                self._channel_right_combo.blockSignals(True)

            try:
                # Reset to default channels [0, 1] when device changes
                if self._channel_left_combo and self._channel_left_combo.count() > 0:
                    self._channel_left_combo.setCurrentIndex(0)
                if self._channel_right_combo and self._channel_right_combo.count() > 1:
                    self._channel_right_combo.setCurrentIndex(1)

                # Save default channels
                self.config.audio_selected_channels = [0, 1]
            finally:
                # Re-enable signals
                if self._channel_left_combo:
                    self._channel_left_combo.blockSignals(False)
                if self._channel_right_combo:
                    self._channel_right_combo.blockSignals(False)

            # Signal that audio settings changed, requiring beat detector restart
            self.audio_settings_changed.emit()

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
                    info = sd.query_devices(device_id)
                    max_channels = int(info.get("max_input_channels", 2))

                    # If selected channels exceed device capabilities, reset to [0, 1]
                    if left_channel >= max_channels or right_channel >= max_channels:
                        logging.warning(
                            f"Selected channels [{left_channel}, {right_channel}] exceed device max {max_channels}, resetting to [0, 1]"
                        )
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
            self.audio_settings_changed.emit()

    def _on_lock_delay_changed(self, state: int) -> None:
        self.config.lock_delay = state == QtCore.Qt.CheckState.Checked.value
        logging.info(f"Lock delay changed to: {self.config.lock_delay}")
        self.settings_changed.emit()

    def _on_lock_feedback_changed(self, state: int) -> None:
        self.config.lock_feedback = state == QtCore.Qt.CheckState.Checked.value
        logging.info(f"Lock feedback changed to: {self.config.lock_feedback}")
        self.settings_changed.emit()

    def _on_lock_pitch_changed(self, state: int) -> None:
        self.config.lock_pitch = state == QtCore.Qt.CheckState.Checked.value
        logging.info(f"Lock pitch changed to: {self.config.lock_pitch}")
        self.settings_changed.emit()

    def _on_theme_changed(self, index: int) -> None:
        theme_mode = self._theme_combo.itemData(index)
        if theme_mode is not None:
            self.config.theme_mode = theme_mode
            logging.info(f"Theme changed to: {theme_mode}")
            self.settings_changed.emit()

    def _on_brightness_changed(self, value: int) -> None:
        """Handle brightness slider change."""
        if self._backlight.is_available():
            success = self._backlight.set_brightness_percent(value)
            if not success:
                # Disable slider if write failed (permission denied)
                self._brightness_slider.setEnabled(False)

    def __del__(self) -> None:
        # sounddevice cleans up automatically, no explicit cleanup needed
        pass
