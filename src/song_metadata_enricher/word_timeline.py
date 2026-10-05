"""A painted Gantt chart: one draggable duration rectangle per word."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .lyrics import format_timestamp
from .theme import GREEN, RED


class WordTimeline(QWidget):
    selected = Signal(int)
    seek_requested = Signal(float)
    edit_requested = Signal(int, str, float, float)
    drag_started = Signal()
    drag_finished = Signal()
    LABEL_WIDTH = 125
    HEADER_HEIGHT = 40
    ROW_HEIGHT = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.words = []
        self.view_start, self.view_end = 0.0, 1.0
        self.next_start = None
        self.position = 0.0
        self.current = -1
        self.drag = None
        self.invalid = set()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(self.HEADER_HEIGHT + 50)
        self.setAccessibleName("Word timing Gantt chart")
        self.setAccessibleDescription(
            "Up and Down choose words. Left and Right move by 10 ms; Control changes start, Shift changes end. Exact numeric controls are also available."
        )

    def set_words(self, words):
        self.words = words
        self.setMinimumHeight(self.HEADER_HEIGHT + max(1, len(words)) * self.ROW_HEIGHT + 12)
        self.update()

    def set_window(self, start, end, next_start=None):
        self.view_start = start
        self.view_end = max(end, start + 0.001)
        self.next_start = next_start
        self.update()

    def set_selected(self, index):
        self.current = index
        self.update()

    def set_position(self, seconds):
        self.position = seconds
        self.update()

    def time_x(self, seconds):
        return self.LABEL_WIDTH + (seconds - self.view_start) / (
            self.view_end - self.view_start
        ) * max(1, self.width() - self.LABEL_WIDTH - 15)

    def x_time(self, x):
        return self.view_start + (x - self.LABEL_WIDTH) / max(
            1, self.width() - self.LABEL_WIDTH - 15
        ) * (self.view_end - self.view_start)

    def bar_rect(self, index):
        word = self.words[index]
        if word.start is None or word.end is None:
            return None
        return QRectF(
            self.time_x(word.start),
            self.HEADER_HEIGHT + index * self.ROW_HEIGHT + 5,
            max(2, self.time_x(word.end) - self.time_x(word.start)),
            self.ROW_HEIGHT - 10,
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        painter.fillRect(
            QRectF(0, 0, self.LABEL_WIDTH, self.height()), self.palette().alternateBase()
        )
        painter.setPen(self.palette().text().color())
        painter.drawText(
            QRectF(10, 0, self.LABEL_WIDTH - 16, self.HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter,
            "Word",
        )
        # Pick readable ticks at the current horizontal zoom.
        target = (self.view_end - self.view_start) * 95 / max(1, self.width() - self.LABEL_WIDTH)
        exponent = 10 ** math.floor(math.log10(max(target, 0.001)))
        step = next(value * exponent for value in (1, 2, 5, 10) if value * exponent >= target)
        tick = math.ceil(self.view_start / step) * step
        while tick <= self.view_end + step * 0.001:
            x = self.time_x(tick)
            painter.setPen(self.palette().mid().color())
            painter.drawLine(QPointF(x, self.HEADER_HEIGHT - 8), QPointF(x, self.height()))
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(x - 45, 3, 90, 25), Qt.AlignmentFlag.AlignCenter, format_timestamp(tick)
            )
            tick += step
        for index, word in enumerate(self.words):
            y = self.HEADER_HEIGHT + index * self.ROW_HEIGHT
            painter.setPen(self.palette().mid().color())
            painter.drawLine(
                QPointF(0, y + self.ROW_HEIGHT), QPointF(self.width(), y + self.ROW_HEIGHT)
            )
            painter.setPen(self.palette().text().color())
            label = painter.fontMetrics().elidedText(
                word.text + (" (est.)" if word.source == "estimated" else ""),
                Qt.TextElideMode.ElideRight,
                self.LABEL_WIDTH - 18,
            )
            painter.drawText(
                QRectF(10, y, self.LABEL_WIDTH - 18, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            rect = self.bar_rect(index)
            if rect is None:
                painter.drawText(
                    QRectF(self.LABEL_WIDTH + 10, y, 250, self.ROW_HEIGHT),
                    Qt.AlignmentFlag.AlignVCenter,
                    "No word timing yet",
                )
                continue
            active = word.start <= self.position < word.end
            painter.save()
            painter.setClipRect(
                QRectF(self.LABEL_WIDTH, y, self.width() - self.LABEL_WIDTH, self.ROW_HEIGHT)
            )
            color = QColor(
                "#f7dfb0"
                if word.source == "estimated"
                else "#a1ecb8"
                if word.source == "manual"
                else "#d7dfd9"
            )
            if active and word.source != "estimated":
                color = QColor(GREEN)
            if self.palette().base().color().lightness() < 128:
                color = QColor("#555555" if active else "#383838")
            painter.setBrush(color)
            painter.setPen(
                QPen(
                    QColor(
                        RED
                        if index in self.invalid
                        else "#26874b"
                        if index == self.current
                        else "#89988d"
                    ),
                    3 if index in self.invalid else 2 if index == self.current else 1,
                )
            )
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(self.palette().text().color() if self.palette().base().color().lightness() < 128 else QColor("#18303f"))
            painter.save()
            painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
            text = painter.fontMetrics().elidedText(
                word.text, Qt.TextElideMode.ElideRight, max(0, int(rect.width()) - 18)
            )
            painter.drawText(rect.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
            painter.drawLine(
                QPointF(rect.left() + 4, rect.top() + 5),
                QPointF(rect.left() + 4, rect.bottom() - 5),
            )
            painter.drawLine(
                QPointF(rect.right() - 4, rect.top() + 5),
                QPointF(rect.right() - 4, rect.bottom() - 5),
            )
            painter.restore()
        painter.save()
        painter.setClipRect(
            QRectF(self.LABEL_WIDTH, 0, self.width() - self.LABEL_WIDTH, self.height())
        )
        if self.next_start is not None:
            x = self.time_x(self.next_start)
            painter.setPen(QPen(QColor("#a47016"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, self.HEADER_HEIGHT), QPointF(x, self.height()))
        x = self.time_x(self.position)
        painter.setPen(QPen(QColor(RED), 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.restore()

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(
                    QColor(GREEN if self.palette().base().color().lightness() < 128 else "#198647"),
                    3,
                )
            )
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event):
        if not self.words:
            return super().keyPressEvent(event)
        row = max(0, self.current)
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.current = max(
                0, min(len(self.words) - 1, row + (-1 if key == Qt.Key.Key_Up else 1))
            )
            self.selected.emit(self.current)
            self.update()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            word = self.words[row]
            if word.start is not None and word.end is not None:
                modifiers = event.modifiers()
                mode = (
                    "start"
                    if modifiers & Qt.KeyboardModifier.ControlModifier
                    else "end"
                    if modifiers & Qt.KeyboardModifier.ShiftModifier
                    else "move"
                )
                increment = 0.1 if modifiers & Qt.KeyboardModifier.AltModifier else 0.01
                delta = -increment if key == Qt.Key.Key_Left else increment
                self.drag_started.emit()
                self.edit_requested.emit(
                    row,
                    mode,
                    word.start + (delta if mode != "end" else 0),
                    word.end + (delta if mode != "start" else 0),
                )
                self.drag_finished.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def row_at(self, point):
        row = int((point.y() - self.HEADER_HEIGHT) // self.ROW_HEIGHT)
        return row if 0 <= row < len(self.words) else -1

    def edge_at(self, point, rect):
        distance_left, distance_right = abs(point.x() - rect.left()), abs(point.x() - rect.right())
        if min(distance_left, distance_right) <= min(8, max(3, rect.width() / 3)):
            return "start" if distance_left < distance_right else "end"
        return "move"

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        row = self.row_at(event.position())
        if row >= 0:
            self.current = row
            self.selected.emit(row)
            rect = self.bar_rect(row)
            if (
                rect is not None
                and event.position().x() >= self.LABEL_WIDTH
                and rect.contains(event.position())
            ):
                word = self.words[row]
                self.drag = (
                    row,
                    self.edge_at(event.position(), rect),
                    event.position().x(),
                    word.start,
                    word.end,
                    False,
                )
                self.update()
                return
            if event.position().x() < self.LABEL_WIDTH:
                if self.words[row].start is not None:
                    self.seek_requested.emit(self.words[row].start)
                self.update()
                return
        self.seek_requested.emit(
            max(self.view_start, min(self.view_end, self.x_time(event.position().x())))
        )

    def mouseMoveEvent(self, event):
        if self.drag:
            row, mode, x, start, end, begun = self.drag
            if not begun and abs(event.position().x() - x) < 2:
                return
            if not begun:
                self.drag_started.emit()
                self.drag = (row, mode, x, start, end, True)
            delta = self.x_time(event.position().x()) - self.x_time(x)
            self.edit_requested.emit(
                row,
                mode,
                start + delta if mode != "end" else start,
                end + delta if mode != "start" else end,
            )
            return
        row = self.row_at(event.position())
        rect = self.bar_rect(row) if row >= 0 else None
        if (
            rect is not None
            and event.position().x() >= self.LABEL_WIDTH
            and rect.contains(event.position())
        ):
            self.setCursor(
                Qt.CursorShape.SizeHorCursor
                if self.edge_at(event.position(), rect) != "move"
                else Qt.CursorShape.OpenHandCursor
            )
            word = self.words[row]
            self.setToolTip(
                f"{word.text}: {format_timestamp(word.start)} – {format_timestamp(word.end)} · {word.source}"
            )
        else:
            self.unsetCursor()
            self.setToolTip(
                "Click the chart to seek. Drag a rectangle or its edges to change timing."
            )

    def mouseReleaseEvent(self, event):
        if self.drag:
            row, mode, x, start, end, begun = self.drag
            self.drag = None
            if begun:
                self.drag_finished.emit()
            else:
                self.seek_requested.emit(self.words[row].start)
            self.update()
        super().mouseReleaseEvent(event)
