"""Category colors and compact song row painting."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
)

from .batch import CATEGORIES

# Accent, light background, dark background: slate, amber, blue, green.
# Quiet surface tints fit the neutral theme; accents distinguish progress without
# treating missing lyrics as an error. Category names remain in the tooltips/filter.
CATEGORY_COLORS = dict(
    zip(
        CATEGORIES,
        (
            ("#7d8791", "#edf0f2", "#272a2d"),
            ("#a78040", "#f4eee2", "#302b23"),
            ("#628ba5", "#e8eff3", "#242d33"),
            ("#559572", "#e5f1e9", "#232f28"),
        ),
    )
)


class SongLoadButton(QToolButton):
    """Compact, palette-aware MP3/folder glyph with an add badge."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("songLoadButton")
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Load song" if kind == "song" else "Load folder")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.ButtonText)
        painter.setPen(QPen(color, 1.5))

        def outline(points):
            painter.drawPolyline([QPoint(x, y) for x, y in points])

        if self.kind == "song":
            outline([(9, 7), (20, 7), (25, 12), (25, 27), (9, 27), (9, 7)])
            outline([(20, 7), (20, 12), (25, 12)])
            font = QFont(self.font())
            font.setPixelSize(7)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(10, 22, "MP3")
        else:
            outline(
                [(7, 26), (7, 10), (14, 10), (17, 13), (28, 13), (28, 26), (7, 26)]
            )
            painter.drawLine(7, 16, 28, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#53ee87") if self.isEnabled() else self.palette().mid().color())
        painter.drawEllipse(21, 21, 12, 12)
        painter.setPen(QPen(QColor("#12371e") if self.isEnabled() else color, 1.5))
        painter.drawLine(24, 27, 30, 27)
        painter.drawLine(27, 24, 27, 30)
        painter.end()


def category_icon(category):
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(CATEGORY_COLORS[category][0]))
    painter.drawEllipse(2, 2, 10, 10)
    painter.end()
    return QIcon(pixmap)


class SongCategoryDelegate(QStyledItemDelegate):
    @staticmethod
    def dark_mode():
        # The view's transparent stylesheet background can make its Base color
        # black even in light mode. Use the application's window palette instead.
        return QApplication.instance().palette().color(QPalette.ColorRole.Window).lightness() < 128

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.palette.setColor(
            QPalette.ColorRole.Text, QColor("#eeeeee" if self.dark_mode() else "#252c28")
        )

    def paint(self, painter, option, index):
        category = index.data(Qt.ItemDataRole.UserRole)
        if category not in CATEGORY_COLORS:
            return super().paint(painter, option, index)
        accent, light, dark = CATEGORY_COLORS[category]
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        color = QColor(dark if self.dark_mode() else light)
        if hovered:
            color = color.lighter(110)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(QPen(QColor(accent), 2) if selected else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 5, 5)
        painter.restore()
        text_option = QStyleOptionViewItem(option)
        # Retain category color on the selected row; its outline shows selection.
        text_option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        super().paint(painter, text_option, index)
