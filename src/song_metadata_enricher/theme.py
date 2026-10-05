"""Shared light/dark themes and accessible switch controls for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
)

BACKGROUND = "#eceeed"
SURFACE = "#f8f9f8"
TEXT = "#252c28"
MUTED = "#626c65"
GREEN = "#53ee87"
RED = "#ff646e"
ACTIVE = "#d0f5dd"
REVIEW = "#fff1d3"


def timing_selection_color(widget):
    return widget.palette().highlight().color() if widget.palette().base().color().lightness() < 128 else QColor(ACTIVE)


def tone(widget, value):
    if widget.property("tone") == value:
        return widget
    widget.setProperty("tone", value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
    return widget


class ToggleSwitch(QCheckBox):
    """Keeps Qt's checkbox keyboard/accessibility semantics with a pill indicator."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(140)
        self.animation.valueChanged.connect(self._animate)
        self.toggled.connect(self._toggle)

    def _toggle(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self._offset)
        self.animation.setEndValue(1.0 if checked else 0.0)
        if self.isVisible() and not QApplication.instance().property("reducedMotion"):
            self.animation.start()
        else:
            self._animate(1.0 if checked else 0.0)

    def _animate(self, value):
        self._offset = value
        self.update()

    def sizeHint(self):
        return QSize(
            46 + (10 + self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0),
            max(30, self.fontMetrics().height() + 4),
        )

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 24) / 2
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x = self.width() - 44 if rtl else 0
        track = QRectF(x, y, 44, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(
                GREEN
                if self.isChecked() and self.isEnabled()
                else "#b9c0bb"
                if self.isEnabled()
                else "#dce0dd"
            )
        )
        painter.drawRoundedRect(track, 12, 12)
        offset = 1 - self._offset if rtl else self._offset
        painter.setBrush(QColor("#ffffff" if self.isEnabled() else "#eff1ef"))
        painter.drawEllipse(QRectF(x + 3 + 20 * offset, y + 3, 18, 18))
        painter.setPen(
            self.palette().color(
                QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.WindowText,
            )
        )
        text_rect = self.rect().adjusted(0, 0, -54, 0) if rtl else self.rect().adjusted(54, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#198647"), 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(track.adjusted(1, -2, 1, 2), 13, 13)


class ThemeEvents(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.FontChange) and isinstance(
            watched, QPushButton
        ):
            watched.setMinimumHeight(max(32, watched.fontMetrics().height() + 18))
        if event.type() == QEvent.Type.Show and isinstance(watched, QPushButton):
            parent = watched.parentWidget()
            if isinstance(parent, (QDialogButtonBox, QMessageBox)):
                role = parent.buttonRole(watched)
                if role.name in {"AcceptRole", "YesRole"}:
                    tone(watched, "positive")
                elif role.name == "DestructiveRole":
                    tone(watched, "danger")
        return False


class ElidedLabel(QLabel):
    """One-line title with the full value available to accessibility and tooltips."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        super().setText(
            self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, self.width()))
        )

    def resizeEvent(self, event):
        self.setText(self.full_text)
        super().resizeEvent(event)


def apply_theme(app: QApplication, variant="dark", font_size=10, reduced_motion=False):
    if variant == "system":
        variant = "dark" if app.styleHints().colorScheme().name == "Dark" else "light"
    signature = f"{variant}:{font_size}:{reduced_motion}"
    if app.property("themeSignature") == signature:
        return
    app.setProperty("themeSignature", signature)
    app.setProperty("reducedMotion", reduced_motion)
    app.setStyle("Fusion")
    app.setFont(QFont("Noto Sans", font_size))
    palette = QPalette()
    for role, value in {
        QPalette.ColorRole.Window: BACKGROUND,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: "#f0f2f0",
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: "#e5e9e5",
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: "#bceccb",
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.Mid: "#d6dcd7",
        QPalette.ColorRole.PlaceholderText: "#7d8880",
        QPalette.ColorRole.ToolTipBase: TEXT,
        QPalette.ColorRole.ToolTipText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#929b94"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#929b94"))
    if variant == "dark":
        for role, value in {
            QPalette.ColorRole.Window: "#141414",
            QPalette.ColorRole.WindowText: "#eeeeee",
            QPalette.ColorRole.Base: "#1c1c1c",
            QPalette.ColorRole.AlternateBase: "#222222",
            QPalette.ColorRole.Text: "#eeeeee",
            QPalette.ColorRole.Button: "#292929",
            QPalette.ColorRole.ButtonText: "#eeeeee",
            QPalette.ColorRole.Highlight: "#383838",
            QPalette.ColorRole.HighlightedText: "#eeeeee",
            QPalette.ColorRole.Mid: "#353535",
            QPalette.ColorRole.PlaceholderText: "#aaaaaa",
        }.items():
            palette.setColor(role, QColor(value))
    app.setPalette(palette)
    assets = Path(__file__).parent / "assets"
    stylesheet = STYLESHEET.replace("{{assets}}", assets.as_posix())
    import re

    stylesheet = re.sub(
        r"font-size: (\d+)px",
        lambda match: f"font-size: {round(int(match[1]) * font_size / 10)}px",
        stylesheet,
    )
    if variant == "dark":
        replacements = {
            "#eceeed": "#141414",
            "#f8f9f8": "#1c1c1c",
            "#252c28": "#eeeeee",
            "#626c65": "#b8b8b8",
            "#ffffff": "#202020",
            "#e0e5e1": "#181818",
            "#e7ebe7": "#292929",
            "#dce2dc": "#353535",
            "#d4ddd5": "#353535",
            "#c6ead1": "#383838",
            "#203d29": "#eeeeee",
            "#f8faf8": "#303030",
            "#e9eee9": "#292929",
            "#536058": "#c8c8c8",
            "#e3e8e3": "#292929",
            "#e0e6e1": "#292929",
            "#e9ece9": "#202020",
            "#e0e5e0": "#292929",
            "#e8ece8": "#202020",
            "#dce3dd": "#353535",
            "#cfd8d0": "#353535",
            "#e5eae5": "#292929",
            "#dce5dd": "#292929",
            "#e8eee8": "#292929",
            "#d0f5dd": "#383838",
            "#edf0ed": "#222222",
            "#d6ded7": "#353535",
            "#d2dad3": "#353535",
            "#b8c8bc": "#606060",
            "#b9c9bc": "#606060",
            "#d5ddd5": "#353535",
            "#d0d9d1": "#353535",
            "#d6dcd7": "#353535",
            "#e2e7e2": "#3a3a3a",
            "#d5ddd6": "#353535",
            "#bec8c0": "#626262",
            "#9eafa2": "#777777",
            "#d2e4d7": "#454545",
            "#d3e5d7": "#454545",
            "#bceccb": "#383838",
            "#939c95": "#999999",
            "#f2f2f2": "#242424",
            "#333333": "#eeeeee",
            "#196137": "#dddddd",
            "#18833e": "#53ee87",
            "#fff1d3": "#303030",
            "#77501b": "#dddddd",

        }
        stylesheet = re.sub(
            r"#[0-9a-fA-F]{6}", lambda match: replacements.get(match[0], match[0]), stylesheet
        )
    app.setStyleSheet(stylesheet)
    if not hasattr(app, "_theme_events"):
        app._theme_events = ThemeEvents(app)
        app.installEventFilter(app._theme_events)


STYLESHEET = """
QMainWindow, QDialog { background: #eceeed; }
QWidget { color: #252c28; }
QWidget#sidebar { background: #e0e5e1; border-radius: 16px; }
QWidget[card="true"] { background: #f8f9f8; border: 1px solid #dce2dc; border-radius: 14px; }
QLabel { background: transparent; border: none; }
QLabel[role="heading"] { font-size: 22px; font-weight: 700; }
QLabel[role="section"] { font-size: 14px; font-weight: 600; }
QLabel[role="muted"] { color: #626c65; }
QLabel#coverReviewStatus[tone="accepted"] { color: #18833e; }
QLabel[role="eyebrow"] { color: #626c65; font-size: 10px; font-weight: 700; }
QLabel[role="badge"] { border-radius: 10px; padding: 6px 12px; background: #e3e8e3; color: #536058; }
QLabel[tone="warning"] { background: #fff1d3; color: #77501b; border-radius: 10px; padding: 6px 12px; }
QLabel[tone="positive"] { background: #d0f5dd; color: #196137; border-radius: 10px; padding: 6px 12px; }
QLabel#artwork, QLabel#coverPreview { background: #e8ece8; border: 1px solid #d6ded7; border-radius: 12px; }
QPushButton { background: #e7ebe7; border: 1px solid #d2dad3; border-radius: 9px; padding: 8px 13px; font-weight: 600; min-height: 16px; }
QPushButton:hover { background: #dce3dd; border-color: #b8c8bc; }
QPushButton:pressed { background: #cfd8d0; }
QToolButton#songLoadButton { background: #e7ebe7; border: 1px solid #d2dad3; border-radius: 7px; padding: 0; }
QToolButton#songLoadButton:hover { background: #dce3dd; border-color: #b8c8bc; }
QToolButton#songLoadButton:pressed { background: #cfd8d0; }
QToolButton#songLoadButton:focus { border: 1px solid #278348; }
QToolButton#songLoadButton:disabled { background: #e9ece9; border-color: #e0e5e0; }
QPushButton:focus { border: 1px solid #278348; }
QPushButton[tone="positive"] { background: #53ee87; border-color: #43db76; color: #12371e; }
QPushButton[tone="positive"]:hover { background: #72f49c; }
QPushButton[tone="positive"]:pressed { background: #39da70; }
QPushButton[tone="danger"] { background: #ff646e; border-color: #f35662; color: #40141a; }
QPushButton[tone="danger"]:hover { background: #ff8189; }
QPushButton[tone="danger"]:pressed { background: #ee4e5b; }
QPushButton[tone="quiet"] { background: transparent; border-color: transparent; color: #536058; }
QPushButton[tone="quiet"]:hover { background: #e5eae5; }
QPushButton:disabled, QPushButton[tone="positive"]:disabled, QPushButton[tone="danger"]:disabled { background: #e9ece9; color: #939c95; border-color: #e0e5e0; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox { background: #ffffff; border: 1px solid #d4ddd5; border-radius: 8px; padding: 6px 9px; selection-background-color: #bceccb; selection-color: #252c28; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #30935a; }
QLineEdit[tone="warning"] { border-color: #b38b38; }
QLineEdit:disabled, QPlainTextEdit:disabled { background: #edf0ed; color: #939c95; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("{{assets}}/chevron-down.svg"); width: 12px; height: 12px; }
QDoubleSpinBox { padding-right: 26px; }
QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; border-left: 1px solid #d4ddd5; border-top-right-radius: 7px; background: #e7ebe7; }
QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border-left: 1px solid #d4ddd5; border-bottom-right-radius: 7px; background: #e7ebe7; }
QDoubleSpinBox::up-arrow { image: url("{{assets}}/chevron-up.svg"); width: 10px; height: 10px; }
QDoubleSpinBox::down-arrow { image: url("{{assets}}/chevron-down.svg"); width: 10px; height: 10px; }
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #d2e4d7; }
QListWidget, QTableWidget { background: #f8f9f8; border: 1px solid #dce2dc; border-radius: 10px; outline: 0; }
QListWidget#songList { background: transparent; border: none; }
QListWidget#songList::item { border-radius: 5px; margin: 0; padding: 3px 6px; }
QListWidget::item { border-radius: 9px; margin: 2px; padding: 9px; }
QListWidget#candidateList::item { border-radius: 5px; margin: 0; padding: 4px 8px; }
QListWidget::item:hover { background: #e8eee8; }
QListWidget::item:selected { background: #c6ead1; color: #203d29; }
QListWidget#songList::item:selected { background: #f8faf8; border: 1px solid #b9c9bc; }
QTableWidget { gridline-color: #e2e7e2; }
QTableWidget::item:selected { background: #c6ead1; color: #203d29; }
QTableWidget:focus, QListWidget:focus { border: 2px solid #278348; }
QHeaderView::section { background: #e9eee9; color: #536058; border: none; border-bottom: 1px solid #d5ddd5; padding: 9px 8px; font-weight: 600; }
QTableCornerButton::section { background: #e9eee9; border: none; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #bec8c0; min-height: 32px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #9eafa2; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #bec8c0; min-width: 32px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSlider::groove:horizontal { height: 6px; background: #d5ddd6; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #66cd88; border-radius: 3px; }
QSlider::handle:horizontal { background: #32c966; border: 2px solid #f8f9f8; width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; }
QSlider::handle:horizontal:hover { background: #22ae54; }
QProgressBar { background: #e0e7e1; border: none; border-radius: 7px; min-height: 14px; color: #2c4a34; text-align: center; }
QProgressBar::chunk { background: #66df8c; border-radius: 7px; }
QSplitter::handle { background: transparent; width: 10px; height: 10px; }
QSplitter::handle:hover { background: #d3e5d7; border-radius: 4px; }
QMenuBar { background: #eceeed; padding: 3px 10px; }
QMenuBar::item { padding: 5px 10px; border-radius: 6px; }
QMenuBar::item:selected { background: #dce5dd; }
QMenu { background: #f8f9f8; border: 1px solid #d0d9d1; padding: 5px; }
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: #d0f5dd; color: #252c28; }
QStatusBar { background: #eceeed; color: #626c65; }
QStatusBar::item { border: none; }
QTabWidget::pane { border: 1px solid #d4ddd5; border-radius: 12px; background: #f8f9f8; top: -1px; }
QTabBar::tab { background: #e0e6e1; color: #626c65; border: 1px solid #d4ddd5; border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 10px 18px; margin-right: 4px; }
QTabBar::tab:selected { background: #f8f9f8; color: #252c28; border-bottom-color: #f8f9f8; }
QTabBar::tab:hover { color: #196137; }
QToolTip { background: #f2f2f2; color: #333333; border: 1px solid #d4ddd5; padding: 7px; }
"""
