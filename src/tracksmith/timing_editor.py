"""Playback-assisted review and editable word boundaries."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .layouts import WrappingLayout
from .lyrics import alignment_reference, format_timestamp, parse_timestamp
from .model import LyricLine, Word
from .theme import ToggleSwitch, timing_selection_color, tone
from .timing_data import TimingWindow, evenly_spaced_words
from .word_timeline import WordTimeline


class PlaybackDialog(QDialog):
    def __init__(self, player, duration, parent=None, playback_start=0.0, playback_end=None):
        super().__init__(parent)
        self.player = player
        self.duration = duration
        self.closed = False
        self.playback_start = playback_start
        self.playback_end = duration if playback_end is None else playback_end
        self.bounded = playback_start > 0 or self.playback_end < duration
        self.minimum_ms = math.ceil(self.playback_start * 1000 - 1e-7)
        self.maximum_ms = math.floor(self.playback_end * 1000 + 1e-7)
        self._position_guard = False
        self.root_layout = QVBoxLayout(self)
        content = QWidget()
        self.layout = QVBoxLayout(content)
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setWidget(content)
        self.root_layout.addWidget(self.body_scroll)
        self.layout.setContentsMargins(22, 20, 22, 20)
        self.layout.setSpacing(12)
        self.position = QLabel()
        playback = WrappingLayout()
        self.play_button = self.button("Play / pause (Space)", self.toggle_play, playback)
        self.button("Back 2 seconds", lambda: self.seek(-2000), playback)
        self.button("Forward 2 seconds", lambda: self.seek(2000), playback)
        playback.addWidget(self.position)
        self.layout.addLayout(playback)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(self.minimum_ms, self.maximum_ms)
        self.timeline.setAccessibleName("Review section playback position")
        self.loop_check = ToggleSwitch("Loop this section")
        playback.addWidget(self.loop_check)
        self.timeline.sliderMoved.connect(self.seek_to)
        self.timeline.valueChanged.connect(self.seek_to)
        self.layout.addWidget(self.timeline)
        player.positionChanged.connect(self.playback_position)
        self.boundary_timer = QTimer(self)
        self.boundary_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.boundary_timer.setInterval(10)
        self.boundary_timer.timeout.connect(self.enforce_playback)
        if self.bounded:
            player.playbackStateChanged.connect(self.playback_state)
            self.playback_state(player.playbackState())
        QApplication.instance().installEventFilter(self)
        self.playback_position(player.position())

    def button(self, text, handler, layout):
        button = QPushButton(text)
        button.setAutoDefault(False)
        if text in {"Apply word timings", "Close — keep timings", "Stamp and next missing (Enter)"}:
            tone(button, "positive")
        button.clicked.connect(handler)
        layout.addWidget(button)
        return button

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.position() >= self.maximum_ms:
                self.seek_to(self.minimum_ms)
            else:
                self.seek_to(self.player.position())
            self.player.play()

    def seek_to(self, position):
        position = max(self.minimum_ms, min(self.maximum_ms, round(position)))
        if self.bounded and position >= self.maximum_ms:
            self.player.pause()
        self.player.setPosition(position)

    def seek(self, delta):
        self.seek_to(self.player.position() + delta)

    def playback_state(self, state):
        if self.closed:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.enforce_playback()
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.boundary_timer.start()
        else:
            self.boundary_timer.stop()

    def enforce_playback(self):
        if not self.closed:
            self.playback_position(self.player.position())

    def playback_position(self, position):
        if self.closed or self._position_guard:
            return None
        if self.bounded and (
            position < self.minimum_ms
            or position > self.maximum_ms
            or (
                position == self.maximum_ms
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            )
        ):
            self._position_guard = True
            try:
                if position >= self.maximum_ms:
                    if self.loop_check.isChecked():
                        self.player.setPosition(self.minimum_ms)
                        return self.minimum_ms
                    self.player.pause()
                position = max(self.minimum_ms, min(self.maximum_ms, position))
                self.player.setPosition(position)
            finally:
                self._position_guard = False
        self.position.setText(
            f"{format_timestamp(position / 1000)} / {format_timestamp(self.playback_end)}"
        )
        if not self.timeline.isSliderDown():
            blocker = QSignalBlocker(self.timeline)
            self.timeline.setValue(position)
            del blocker
        return position

    def eventFilter(self, watched, event):
        focus = QApplication.focusWidget()
        if (
            event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
            and self.isActiveWindow()
            and (focus is None or focus.window() is self)
            and event.key() == Qt.Key.Key_Space
            and not event.modifiers()
        ):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self.toggle_play()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.isActiveWindow()
            and focus
            and focus.window() is self
            and not isinstance(focus, (QLineEdit, QAbstractSpinBox, ToggleSwitch))
        ):
            if (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_Z
                and hasattr(self, "undo")
            ):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and hasattr(self, "redo"):
                    self.redo()
                else:
                    self.undo()
                return True
            if event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            ):
                return False
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.isAutoRepeat():
                return True
            if isinstance(focus, QPushButton):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    focus.click()
                    return True
                return False
            if isinstance(focus, QSlider):
                return False
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.stamp(bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
            if event.key() == Qt.Key.Key_Backspace and hasattr(self, "undo"):
                self.undo()
                return True
        return super().eventFilter(watched, event)

    def done(self, result):
        if not self.closed:
            self.closed = True
            self.boundary_timer.stop()
            self.player.pause()
            self.player.positionChanged.disconnect(self.playback_position)
            if self.bounded:
                self.player.playbackStateChanged.disconnect(self.playback_state)
            QApplication.instance().removeEventFilter(self)
        super().done(result)


@dataclass
class WordDraft:
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float = 0.0
    source: str = "ai"
    start_edited: bool = False
    end_edited: bool = False

    def set_boundary(self, boundary: str, value: float | None):
        setattr(self, boundary, value)
        setattr(self, boundary + "_edited", True)
        if self.start_edited and self.end_edited:
            self.confidence = 1.0
            self.source = "manual"
        elif self.source == "ai":
            self.source = "mixed"


class WordTimingDialog(PlaybackDialog):
    def __init__(
        self,
        line: LyricLine,
        player,
        duration: float,
        parent=None,
        next_start=None,
        window: TimingWindow | None = None,
    ):
        if window is None:
            start = line.start if line.start is not None else 0.0
            end = next_start if next_start is not None else duration
            window = TimingWindow(
                start, end, start, line.start is not None or next_start is not None
            )
        self.window = window
        super().__init__(player, duration, parent, window.start, window.end)
        self.setWindowTitle("Word timings — selected lyric line")
        self.resize(1240, 860)
        self.original = line
        self.result_line = None
        self.drafts = (
            [WordDraft(**asdict(word)) for word in line.words]
            if line.words
            else [
                WordDraft(text, source="untimed") for text in alignment_reference(line.text).split()
            ]
        )
        self.modified = False
        self.user_changed = False
        self.history = []
        self.redo_history = []
        self.next_start = next_start
        label = QLabel(line.text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setProperty("role", "section")
        label.setWordWrap(True)
        self.layout.addWidget(label)
        self.spacing_start = window.word_start
        self.spacing_end = min(
            next_start if next_start is not None else line.end if line.end is not None else window.end,
            window.end,
        )
        self.interval_hint = QLabel(
            f"Review and playback window: {format_timestamp(window.start)} – {format_timestamp(window.end)}. "
            + (
                "Bounded by the surrounding timed lines. "
                if line.start is None and window.anchored
                else ""
            )
            + "Word ranges stay inside this window."
        )
        self.interval_hint.setProperty("role", "badge")
        self.interval_hint.setWordWrap(True)
        self.layout.addWidget(self.interval_hint)
        tools = WrappingLayout()
        self.button("Space words evenly", self.space_evenly, tools)
        self.undo_button = self.button("Undo", self.undo, tools)
        self.redo_button = self.button("Redo", self.redo, tools)
        self.button("Zoom in", lambda: self.zoom(1.6), tools)
        self.button("Zoom out", lambda: self.zoom(1 / 1.6), tools)
        self.button("Fit line", lambda: self.graph.setMinimumWidth(0), tools)
        self.join_check = ToggleSwitch("Keep neighbouring words joined")
        self.join_check.setChecked(True)
        tools.addWidget(self.join_check)
        self.layout.addLayout(tools)
        self.graph = WordTimeline()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.graph)
        self.scroll.setMinimumHeight(160)
        self.layout.addWidget(self.scroll, 1)
        self.validation_state = QLabel()
        self.validation_state.setWordWrap(True)
        self.layout.addWidget(self.validation_state)
        self.table = QTableWidget(len(self.drafts), 4)
        self.table.setHorizontalHeaderLabels(["Word", "Start", "End", "Support"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMaximumHeight(165)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setShowGrid(False)
        self.exact_check = ToggleSwitch("Show exact timestamps / tap controls")
        self.layout.addWidget(self.exact_check)
        self.layout.addWidget(self.table)
        self.table.hide()
        self.exact_check.toggled.connect(self.table.setVisible)
        self.table.itemChanged.connect(self.edited)
        self.table.cellClicked.connect(self.seek_word)
        self.table.itemSelectionChanged.connect(
            lambda: self.graph.set_selected(self.table.currentRow())
        )
        self.graph.selected.connect(self.table.selectRow)
        self.graph.seek_requested.connect(lambda seconds: self.seek_to(round(seconds * 1000)))
        self.graph.drag_started.connect(self.remember)
        self.graph.edit_requested.connect(self.drag_edit)
        self.graph.drag_finished.connect(self.finish_drag)
        inspector = WrappingLayout()
        self.selected_word_label = QLabel("Selected word")
        self.selected_word_label.setTextFormat(Qt.TextFormat.PlainText)
        inspector.addWidget(self.selected_word_label)
        self.word_start = QDoubleSpinBox()
        self.word_end = QDoubleSpinBox()
        for name, spin, column in (("Start", self.word_start, 1), ("End", self.word_end, 2)):
            spin.setDecimals(3)
            spin.setRange(window.start, window.end)
            spin.setSingleStep(0.01)
            spin.setSuffix(" s")
            spin.setAccessibleName(f"Selected word {name.lower()}")
            inspector.addWidget(QLabel(name))
            inspector.addWidget(spin)
            spin.editingFinished.connect(
                lambda field=spin, col=column: self.inspect_edit(field, col)
            )
        self.layout.addLayout(inspector)
        self.table.itemSelectionChanged.connect(self.update_inspector)
        actions = WrappingLayout()
        for button in (
            self.button("Set start (Enter)", lambda: self.stamp(False), actions),
            self.button("Set end + next (Shift+Enter)", lambda: self.stamp(True), actions),
        ):
            button.hide()
            self.exact_check.toggled.connect(button.setVisible)
        self.layout.addLayout(actions)
        self.message = QLabel(
            "Gray: aligned / partly adjusted · amber: estimated · green: manual. Equal spacing is a starting guess, not audio analysis."
        )
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)
        self.review_check = ToggleSwitch(
            "I have reviewed all word timings for this line by listening"
        )
        self.review_check.toggled.connect(lambda: setattr(self, "user_changed", True))
        self.layout.addWidget(self.review_check)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.apply_button = self.button("Apply word timings", self.apply_words, buttons)
        self.button("Cancel", self.reject, buttons)
        self.root_layout.addLayout(buttons)
        self.render()
        if self.drafts:
            self.table.selectRow(0)
        self.update_window()
        if not line.words and window.anchored:
            self.space_evenly()
        self.user_changed = False

    def inspect_edit(self, spin, column):
        row = self.table.currentRow()
        if row >= 0:
            self.table.item(row, column).setText(format_timestamp(spin.value()))

    def update_inspector(self):
        row = self.table.currentRow()
        if row >= 0 and hasattr(self, "word_start"):
            word = self.drafts[row]
            duration = (
                f"{word.end - word.start:.3f} s"
                if word.start is not None and word.end is not None
                else "incomplete"
            )
            self.selected_word_label.setText(
                f"Word {row + 1}: {word.text} · {word.source} · duration {duration}"
            )
            self.word_start.setValue(word.start if word.start is not None else self.window.start)
            self.word_end.setValue(word.end if word.end is not None else self.window.end)

    def confirm_discard(self):
        return (
            QMessageBox.question(
                self,
                "Discard word draft",
                "Discard your unapplied word edits?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Discard
        )

    def done(self, result):
        if (
            result == QDialog.DialogCode.Rejected
            and self.user_changed
            and not self.confirm_discard()
        ):
            return
        super().done(result)

    def remember(self):
        self.user_changed = True
        self.history.append((deepcopy(self.drafts), self.modified))
        self.redo_history.clear()

    def undo(self):
        if self.history:
            self.redo_history.append((deepcopy(self.drafts), self.modified))
            self.drafts, self.modified = self.history.pop()
            self.review_check.setChecked(False)
            self.render()

    def redo(self):
        if self.redo_history:
            self.history.append((deepcopy(self.drafts), self.modified))
            self.drafts, self.modified = self.redo_history.pop()
            self.review_check.setChecked(False)
            self.render()

    def space_evenly(self):
        try:
            words = evenly_spaced_words(
                self.original.text,
                max(self.spacing_start, self.window.start),
                self.spacing_end,
                self.duration,
            )
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.remember()
        self.drafts = [WordDraft(**asdict(word)) for word in words]
        self.modified = True
        self.review_check.setChecked(False)
        self.update_window()
        self.render()
        self.message.setText(
            "Words spaced evenly as estimates. Drag their edges to match the singing, then review and apply."
        )

    def update_window(self):
        self.graph.set_window(
            self.window.start,
            self.window.end,
            self.next_start,
        )

    def zoom(self, factor):
        width = min(20000, round(self.graph.width() * factor))
        self.graph.setMinimumWidth(width if width > self.scroll.viewport().width() else 0)

    def drag_edit(self, index, mode, start, end):
        word = self.drafts[index]
        if word.start is None or word.end is None:
            return
        previous = self.drafts[index - 1] if index > 0 else None
        following = self.drafts[index + 1] if index + 1 < len(self.drafts) else None
        left_joined = (
            self.join_check.isChecked()
            and previous is not None
            and previous.end is not None
            and previous.start is not None
            and abs(previous.end - word.start) < 0.002
        )
        right_joined = (
            self.join_check.isChecked()
            and following is not None
            and following.start is not None
            and following.end is not None
            and abs(following.start - word.end) < 0.002
        )
        minimum = 0.01
        lower = (
            (previous.start + minimum if left_joined else previous.end)
            if previous is not None and previous.end is not None
            else 0
        )
        upper = (
            (following.end - minimum if right_joined else following.start)
            if following is not None and following.start is not None
            else self.duration
        )
        lower, upper = (
            max(self.window.start, lower),
            min(self.window.end, upper),
        )
        if mode == "start":
            if word.end - minimum < lower:
                return
            start, end = max(lower, min(word.end - minimum, start)), word.end
        elif mode == "end":
            if upper < word.start + minimum:
                return
            start, end = word.start, max(word.start + minimum, min(upper, end))
        else:
            width = end - start
            if upper - lower < width:
                return
            start = max(lower, min(upper - width, start))
            end = start + width
        if mode in ("start", "move"):
            word.set_boundary("start", start)
            if left_joined:
                previous.set_boundary("end", start)
        if mode in ("end", "move"):
            word.set_boundary("end", end)
            if right_joined:
                following.set_boundary("start", end)
        self.modified = True
        self.review_check.setChecked(False)
        self.render()
        self.message.setText(
            f"{word.text}: {format_timestamp(start)} – {format_timestamp(end)}. "
            + ("Joined neighbouring boundaries moved too. " if left_joined or right_joined else "")
            + "Undo reverses the whole edit."
        )

    def finish_drag(self):
        if self.history and self.history[-1][0] == self.drafts:
            self.history.pop()
        self.update_window()
        self.render()

    def render(self):
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(self.drafts))
        for row, word in enumerate(self.drafts):
            for column, text in enumerate(
                (
                    word.text,
                    format_timestamp(word.start),
                    format_timestamp(word.end),
                    "Estimated"
                    if word.source == "estimated"
                    else "Manual"
                    if word.source == "manual"
                    else f"{word.confidence:.0%}"
                    if word.start is not None and word.end is not None
                    else "Untimed",
                )
            ):
                item = QTableWidgetItem(text)
                if column in (0, 3):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        del blocker
        self.graph.set_words(self.drafts)
        issue = self.draft_issue()
        self.validation_state.setText(issue[1] if issue else "")
        self.validation_state.setVisible(issue is not None)
        self.graph.invalid = {issue[0]} if issue else set()
        if issue and "starts before word" in issue[1]:
            self.graph.invalid.add(issue[0] - 1)
        self.graph.set_selected(self.table.currentRow())
        self.update_inspector()
        self.undo_button.setEnabled(bool(self.history))
        self.redo_button.setEnabled(bool(self.redo_history))
        self.playback_position(self.player.position())

    def draft_issue(self):
        for row, word in enumerate(self.drafts):
            issue = None
            if word.start is None or word.end is None:
                issue = "needs both a start and an end"
            elif not self.window.start <= word.start < word.end <= self.window.end:
                issue = "needs a start before its end inside this review window"
            elif (
                row
                and self.drafts[row - 1].end is not None
                and word.start < self.drafts[row - 1].end
            ):
                issue = (
                    f"starts before word {row} ends at {format_timestamp(self.drafts[row - 1].end)}"
                )
            if issue:
                return row, f"Word {row + 1} ({word.text}) {issue}."
        if not self.drafts:
            return 0, "This line has no words to time."
        return None

    def playback_position(self, position):
        position = super().playback_position(position)
        if position is None:
            return
        if hasattr(self, "graph"):
            self.graph.set_position(position / 1000)
        if not hasattr(self, "table"):
            return
        blocker = QSignalBlocker(self.table)
        for row, word in enumerate(self.drafts):
            active = (
                word.start is not None
                and word.end is not None
                and word.start <= position / 1000 < word.end
            )
            for column in range(4):
                item = self.table.item(row, column)
                if item:
                    item.setBackground(timing_selection_color(self.table) if active else self.palette().base().color())
                    item.setForeground(QColor("#202020") if active and self.palette().base().color().lightness() >= 128 else self.palette().text().color())
        del blocker

    def edited(self, item):
        if item.column() not in (1, 2):
            return
        try:
            value = parse_timestamp(item.text())
            if value is not None and not self.window.start <= value <= self.window.end:
                raise ValueError("Word timestamp must be inside the audio review window")
            self.remember()
            self.drafts[item.row()].set_boundary("start" if item.column() == 1 else "end", value)
            self.modified = True
            self.review_check.setChecked(False)
            self.message.setText(
                "Edited word boundary. Apply keeps this draft; Cancel discards it."
            )
        except ValueError as exc:
            self.message.setText(str(exc))
        self.render()

    def stamp(self, shifted=False):
        row = self.table.currentRow()
        if row < 0:
            return
        word = self.drafts[row]
        value = self.player.position() / 1000
        if shifted and (word.start is None or value <= word.start):
            self.message.setText("Set the start first; the end must be later than the start.")
            return
        self.remember()
        word.set_boundary("end" if shifted else "start", value)
        self.modified = True
        self.render()
        self.table.selectRow(min(row + (1 if shifted else 0), len(self.drafts) - 1))
        self.message.setText(
            "Word end set; next word selected."
            if shifted
            else "Word start set; press Shift+Enter when it ends."
        )

    def seek_word(self, row, column=0):
        word = self.drafts[row]
        value = word.end if column == 2 else word.start
        if value is not None:
            self.seek_to(round(value * 1000))

    def play_lead_in(self):
        start = (
            self.drafts[0].start
            if self.drafts and self.drafts[0].start is not None
            else self.original.start
        )
        self.player.pause()
        self.seek_to(round(max(self.window.start, (start or self.window.start) - 2) * 1000))
        self.toggle_play()

    def apply_words(self):
        if not self.modified and not self.review_check.isChecked():
            self.reject()
            return
        issue = self.draft_issue()
        if issue:
            row, message = issue
            self.table.selectRow(row)
            self.exact_check.setChecked(True)
            self.scroll.ensureVisible(0, self.graph.HEADER_HEIGHT + row * self.graph.ROW_HEIGHT)
            self.message.setText(message)
            self.validation_state.setText(message)
            self.body_scroll.ensureWidgetVisible(self.validation_state)
            return
        if not self.drafts or any(word.start is None or word.end is None for word in self.drafts):
            self.message.setText(
                "Set both start and end for every word before applying. Cancel keeps the original line unchanged."
            )
            return
        if any(not 0 <= word.start < word.end <= self.duration for word in self.drafts):
            self.message.setText(
                "Each word needs a start before its end, within the audio duration."
            )
            return
        if any(
            word.start < self.window.start or word.end > self.window.end for word in self.drafts
        ):
            self.message.setText(
                "All words must stay inside the review window bounded by the surrounding lines."
            )
            return
        if any(b.start < a.end for a, b in zip(self.drafts, self.drafts[1:])):
            self.message.setText(
                "Word spans must be in order without overlap. Correct the starts/ends before applying."
            )
            return
        self.result_line = deepcopy(self.original)
        self.result_line.words_reviewed = self.review_check.isChecked()
        self.result_line.line_reviewed = self.review_check.isChecked()
        if not self.modified:
            self.accept()
            return
        self.result_line.words = [
            Word(word.text, word.start, word.end, word.confidence, word.source)
            for word in self.drafts
        ]
        self.result_line.start = self.drafts[0].start
        self.result_line.end = self.drafts[-1].end
        # Editing one word cannot certify the rest of an AI-aligned line.
        self.result_line.confidence = min(
            min(word.confidence for word in self.drafts),
            1.0
            if all(word.source == "manual" for word in self.drafts)
            else self.original.confidence,
        )
        estimated = sum(word.source == "estimated" for word in self.drafts)
        self.result_line.source = "estimated-words" if estimated else "manual-words"
        self.result_line.note = (
            f"{estimated} word timings are spacing estimates; review by listening."
            if estimated
            else "Word boundaries edited manually; unchanged word scores remain AI support."
        )
        self.accept()
