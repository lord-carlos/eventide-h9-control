"""Theme management for the H9 Control application."""

from __future__ import annotations

import logging
import os

from PySide6 import QtGui, QtWidgets


def detect_system_theme() -> str:
    """Detect if system prefers dark mode. Returns 'dark' or 'light'."""
    # Check for standard Linux desktop environment indicators
    xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    gtk_theme = os.environ.get("GTK_THEME", "").lower()

    # Check for dark theme indicators
    dark_indicators = ["dark", "night", "black"]

    # Check GTK theme name
    if any(indicator in gtk_theme for indicator in dark_indicators):
        return "dark"

    # Check if we're in a known dark DE
    if any(de in xdg_desktop for de in ["gnome", "kde", "xfce", "mate", "cinnamon"]):
        # These DEs usually set GTK_THEME or have a gsettings/dconf value
        # For now, default to light unless explicitly detected as dark
        pass

    # Default to light on systems without standard theme detection
    return "light"


def _create_dark_palette() -> QtGui.QPalette:
    """Create a dark color palette."""
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(35, 35, 35))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(42, 130, 218))
    palette.setColor(
        QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(255, 255, 255)
    )
    return palette


def _create_light_palette() -> QtGui.QPalette:
    """Create a light color palette."""
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(0, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(255, 255, 255))
    palette.setColor(
        QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(240, 240, 240)
    )
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(255, 255, 220))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(0, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(0, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(240, 240, 240))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(0, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(42, 130, 218))
    palette.setColor(
        QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(255, 255, 255)
    )
    return palette


def _create_darker_palette() -> QtGui.QPalette:
    """Create a very dark palette (more black)."""
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(18, 18, 18))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(40, 40, 40))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(55, 55, 55))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(40, 40, 40))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(25, 25, 25))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 50, 50))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(0, 150, 255))
    palette.setColor(
        QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(255, 255, 255)
    )
    return palette


def _create_crazy_palette() -> QtGui.QPalette:
    """Create a wild neon theme."""
    palette = QtGui.QPalette()
    # Yellow background for windows (dropdown menus, etc.)
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(255, 255, 0))
    # Black text so it's visible on yellow
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(0, 0, 0))
    # Same yellow for base elements
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(255, 255, 0))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(255, 200, 0))
    # Neon cyan tooltips
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(0, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(255, 0, 128))
    # Black text for input fields
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(0, 0, 0))
    # Neon pink buttons
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(255, 0, 128))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(0, 255, 255))
    # Bright yellow accents
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 255, 0))
    # Neon magenta highlight
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(255, 0, 255))
    palette.setColor(
        QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(0, 255, 255)
    )
    return palette


def apply_theme(app: QtWidgets.QApplication, mode: str) -> None:
    """Apply light or dark theme to the application.

    Args:
        app: The QApplication instance
        mode: One of "system", "light", "dark", "darker", or "crazy"
              "system" uses the OS palette (default Qt behavior)
    """
    if mode == "system":
        # Use OS default palette - reset to system default
        app.setPalette(QtGui.QPalette())
        app.setStyle("Fusion")
        logging.info("Applied system theme (OS default)")
    elif mode == "dark":
        palette = _create_dark_palette()
        app.setPalette(palette)
        app.setStyle("Fusion")
        logging.info("Applied dark theme")
    elif mode == "darker":
        palette = _create_darker_palette()
        app.setPalette(palette)
        app.setStyle("Fusion")
        logging.info("Applied darker theme")
    elif mode == "crazy":
        palette = _create_crazy_palette()
        app.setPalette(palette)
        app.setStyle("Fusion")
        logging.info("Applied crazy theme")
    else:  # light
        palette = _create_light_palette()
        app.setPalette(palette)
        app.setStyle("Fusion")
        logging.info("Applied light theme")
