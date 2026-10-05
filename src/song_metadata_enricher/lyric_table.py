"""A lyric table whose cursor belongs to playback while audio is running."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QTableWidget


class LyricTable(QTableWidget):
    selection_blocked = Signal()
    playback_line_clicked = Signal(int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playback_locked = False
        self._paused_edit_triggers = self.editTriggers()
        self._paused_tab_navigation = self.tabKeyNavigation()

    def set_playback_locked(self, locked):
        focus = QApplication.focusWidget()
        if locked and focus and self.isAncestorOf(focus):
            self.setFocus()  # Commit any paused cell editor before playback takes the cursor.
        self.playback_locked = locked
        self.setTabKeyNavigation(False if locked else self._paused_tab_navigation)
        self.setEditTriggers(
            self.EditTrigger.NoEditTriggers if locked else self._paused_edit_triggers
        )
        self.setToolTip(
            "Click a timed line to pause at its start. Pause to edit another line."
            if locked
            else "Select a line to seek; double-click to edit. Enter stamps this selected line if untimed."
        )

    def mousePressEvent(self, event):
        if self.playback_locked:
            index = self.indexAt(event.position().toPoint())
            if event.button() == Qt.MouseButton.LeftButton and index.isValid():
                self.playback_line_clicked.emit(index.row(), index.column())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.playback_locked:
            self.selection_blocked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self.playback_locked and event.buttons():
            event.accept()
            return
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if self.playback_locked and (
            event.key()
            in {
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_Left,
                Qt.Key.Key_Right,
                Qt.Key.Key_Home,
                Qt.Key.Key_End,
                Qt.Key.Key_PageUp,
                Qt.Key.Key_PageDown,
                Qt.Key.Key_Escape,
                Qt.Key.Key_F2,
            }
            or (
                event.key() == Qt.Key.Key_A
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            )
        ):
            self.selection_blocked.emit()
            event.accept()
            return
        super().keyPressEvent(event)
