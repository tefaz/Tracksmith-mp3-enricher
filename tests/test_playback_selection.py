from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView
from test_stamping import stamping_window

from tracksmith.theme import timing_selection_color


def assert_one_target(window):
    row = window.table.currentRow()
    highlighted = [
        index
        for index in range(window.table.rowCount())
        if window.table.item(index, 0).background().color().name() == timing_selection_color(window.table).name()
    ]
    assert highlighted == [row]
    if window.track.aligned_lines[row].start is None:
        assert window.stamp_target() == row
    else:
        assert window.stamp_target() is None
        assert not window.stamp_button.isEnabled()


def test_playback_keyboard_and_review_controls_cannot_select_or_seek(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines)
    window.player.setPosition(1100)
    window.player.play()
    assert window.table.playback_locked
    assert window.table.currentRow() == 2
    assert_one_target(window)
    other = window.table.visualItemRect(window.table.item(0, 1)).center()
    qtbot.mouseDClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=other)
    assert window.table.state() != QAbstractItemView.State.EditingState
    for key in (
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
    ):
        qtbot.keyClick(window.table, key)
        assert window.table.currentRow() == 2
    assert not window.review_filter.isEnabled()
    window.seek_line(0)
    assert window.table.currentRow() == 2
    assert window.player.position() >= 1100
    assert window.track.aligned_lines == original
    assert_one_target(window)
    window.player.pause()
    assert not window.table.playback_locked
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=other)
    assert window.table.currentRow() == 0
    assert window.player.position() == 200
    assert_one_target(window)
    qtbot.keyClick(window.table, Qt.Key.Key_Down)
    assert window.table.currentRow() == 1
    assert_one_target(window)
    window.close()


def test_click_timed_line_during_playback_pauses_at_its_start(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines)
    window.player.setPosition(1100)
    window.player.play()
    other = window.table.visualItemRect(window.table.item(0, 1)).center()
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=other)
    assert not window.is_playing()
    assert not window.table.playback_locked
    assert window.player.position() == 200
    assert window.table.currentRow() == 0
    assert_one_target(window)
    assert window.track.aligned_lines == original
    window.player.play()
    assert window.is_playing()
    assert window.table.currentRow() == 0
    window.player.pause()
    window.close()


def test_click_untimed_line_during_playback_keeps_playing(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.player.setPosition(220)
    window.player.play()
    other = window.table.visualItemRect(window.table.item(2, 1)).center()
    qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=other)
    assert window.is_playing()
    assert window.table.currentRow() == 0
    window.player.pause()
    window.close()


def test_pause_manual_choice_resume_removes_the_old_pinned_selection(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    window.seek_line(0)
    window.player.setPosition(1100)
    assert window.table.currentRow() == 0  # Paused selection does not follow timeline seeks.
    assert_one_target(window)
    window.player.play()
    assert window.table.currentRow() == 2  # Resume follows the audio immediately.
    assert_one_target(window)
    window.player.pause()
    window.table.selectRow(4)
    window.player.setPosition(2900)
    window.player.play()
    assert window.table.currentRow() == 4
    assert_one_target(window)
    window.player.pause()
    window.close()


def test_known_words_and_the_missing_gap_use_the_same_selected_highlight(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.player.setPosition(220)
    window.player.play()
    assert window.table.currentRow() == 0
    assert_one_target(window)
    window.player.setPosition(650)
    assert window.table.currentRow() == 1
    assert_one_target(window)
    window.player.setPosition(1100)
    assert window.table.currentRow() == 2
    assert_one_target(window)
    window.player.setPosition(2550)
    assert window.table.currentRow() == 3  # Missing third's window has passed.
    assert_one_target(window)
    window.player.setPosition(2850)
    assert window.table.currentRow() == 4
    assert_one_target(window)
    window.player.pause()
    window.close()


def test_enter_writes_only_the_selected_line_and_never_an_unselected_pending_line(
    qtbot, mp3, tmp_path
):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    window.player.setPosition(1100)
    original = deepcopy(window.track.aligned_lines)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines == original
    window.player.play()
    row = window.table.currentRow()
    assert row == 2
    assert_one_target(window)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines[row].start is not None
    assert window.track.aligned_lines[:2] == original[:2]
    assert window.track.aligned_lines[3:] == original[3:]
    assert_one_target(window)
    window.player.pause()
    window.close()


def test_tab_and_ctrl_navigation_do_not_change_the_playing_row(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.player.setPosition(1100)
    window.player.play()
    for key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        qtbot.keyClick(window.table, key)
        assert window.table.currentRow() == 2
    qtbot.keyClick(window.table, Qt.Key.Key_A, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(window.table, Qt.Key.Key_Up, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.table.currentRow() == 2
    assert_one_target(window)
    window.player.pause()
    assert window.table.tabKeyNavigation()
    window.close()


def test_starting_playback_closes_a_paused_cell_editor(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.editItem(window.table.item(0, 1))
    qtbot.waitUntil(lambda: window.table.state() == QAbstractItemView.State.EditingState)
    window.player.setPosition(1100)
    window.player.play()
    assert window.table.state() != QAbstractItemView.State.EditingState
    assert window.table.currentRow() == 2
    assert_one_target(window)
    window.player.pause()
    window.close()


def test_words_cell_opens_clicked_line_paused_or_playing(qtbot, mp3, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from tracksmith.timing_editor import WordTimingDialog

    window = stamping_window(qtbot, mp3, tmp_path)
    opened = []

    def inspect_dialog(dialog):
        assert not window.is_playing()
        opened.append(dialog.original)
        dialog.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(WordTimingDialog, "exec", inspect_dialog)
    for playing, row in ((False, 1), (True, 0), (True, 2)):
        window.player.setPosition(1100)
        if playing:
            window.player.play()
        cell = window.table.visualItemRect(window.table.item(row, 3)).center()
        qtbot.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=cell)
        assert opened[-1] is window.track.aligned_lines[row]
        assert window.table.currentRow() == row
        assert not window.is_playing()
    assert len(opened) == 3
    window.close()
