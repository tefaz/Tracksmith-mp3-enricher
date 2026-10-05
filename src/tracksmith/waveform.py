"""Local amplitude overview; audio decoding runs in a cancellable worker."""

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


def decode_overview(path, duration, context, cache, identity):
    context.check()
    previous = cache.get("waveform", {"content": identity, "version": 1})
    if previous:
        return previous
    # A bounded overview rate keeps even long recordings manageable. It is not timing evidence.
    rate = max(100, min(4000, int(4_000_000 / max(duration, 1))))
    raw = context.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    samples = np.frombuffer(raw, dtype="<f4")
    if not len(samples):
        raise ValueError("No decodable audio for waveform")
    context.check()
    chunks = np.array_split(samples, min(4096, len(samples)))
    result = [[float(chunk.min()), float(chunk.max())] for chunk in chunks]
    cache.put("waveform", {"content": identity, "version": 1}, result)
    return result


class Waveform(QWidget):
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks = []
        self.duration = 0.0
        self.position = 0.0
        self.view_start = 0.0
        self.view_end = 1.0
        self.selection = None
        self.boundaries = []
        self.message = "Waveform loads locally when shown"
        self.setMinimumHeight(80)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Audio amplitude waveform")
        self.setAccessibleDescription(
            "Click to seek. Left and Right seek one second. Plus/Minus zoom. Amplitude is not proof of lyric boundaries."
        )

    def set_audio(self, duration, peaks=None):
        self.duration = duration
        self.peaks = peaks or []
        self.view_start, self.view_end = 0, max(duration, 0.001)
        self.update()

    def zoom(self, factor):
        width = min(self.duration, max(1, (self.view_end - self.view_start) / factor))
        self.view_start = max(0, min(self.duration - width, self.position - width / 2))
        self.view_end = self.view_start + width
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        width = max(0.001, self.view_end - self.view_start)

        def x_time(time):
            return (time - self.view_start) / width * self.width()

        if self.selection:
            a, b = self.selection
            painter.fillRect(
                int(x_time(a)),
                0,
                int(x_time(b) - x_time(a)),
                self.height(),
                QColor(102, 205, 136, 60),
            )
        painter.setPen(QPen(QColor("#53a975")))
        mid = self.height() / 2
        for x in range(self.width()):
            seconds = self.view_start + x / max(1, self.width()) * width
            index = int(seconds / max(0.001, self.duration) * len(self.peaks))
            if 0 <= index < len(self.peaks):
                low, high = self.peaks[index]
                painter.drawLine(x, int(mid - high * mid), x, int(mid - low * mid))
        painter.setPen(QPen(self.palette().text().color(), 1, Qt.PenStyle.DotLine))
        for time in self.boundaries:
            if self.view_start <= time <= self.view_end:
                painter.drawLine(int(x_time(time)), 0, int(x_time(time)), self.height())
        painter.setPen(QPen(QColor("#ff646e"), 2))
        painter.drawLine(int(x_time(self.position)), 0, int(x_time(self.position)), self.height())
        if not self.peaks:
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.seek_requested.emit(
                max(
                    0,
                    min(
                        self.duration,
                        self.view_start
                        + event.position().x()
                        / max(1, self.width())
                        * (self.view_end - self.view_start),
                    ),
                )
            )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.seek_requested.emit(
                max(
                    0,
                    min(
                        self.duration, self.position + (-1 if event.key() == Qt.Key.Key_Left else 1)
                    ),
                )
            )
        elif event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(2)
        elif event.key() == Qt.Key.Key_Minus:
            self.zoom(0.5)
        else:
            super().keyPressEvent(event)
