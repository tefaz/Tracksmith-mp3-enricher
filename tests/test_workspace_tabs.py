from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
from test_ui import window_for

from tracksmith.model import LyricLine
from tracksmith.tags import read_track
from tracksmith.theme import apply_theme


@pytest.mark.parametrize("variant", ["dark", "light"])
def test_workspace_tabs_keep_drafts_and_show_file_information(qtbot, mp3, tmp_path, variant):
    window = window_for(qtbot, mp3, tmp_path)
    app = QApplication.instance()
    try:
        apply_theme(app, variant)
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        assert window.workspace_tabs.count() == 2
        assert window.workspace_tabs.currentIndex() == 0
        assert window.workspace_tabs.tabText(0) == "Lyrics && timing"
        assert window.workspace_tabs.tabText(1) == "Song details"
        assert window.lyrics_editor.isVisible()
        assert window.table.isVisible()
        assert not window.metadata_fields["title"].isVisible()
        window.lyrics_editor.setPlainText("First line\nSecond line")
        window.apply_button.click()
        original = deepcopy(window.track.aligned_lines)
        window.resize(1280, 800)
        window.grab().save(str(tmp_path / f"lyrics-tab-{variant}.png"))
        window.workspace_tabs.setCurrentIndex(1)
        assert not window.lyrics_editor.isVisible()
        assert window.metadata_fields["title"].isVisible()
        assert window.save_button.isVisible()
        assert window.file_details["filename"].text() == mp3.name
        assert window.file_details["location"].text() == str(mp3.parent)
        assert window.file_details["sample_rate"].text() == "44,100 Hz"
        assert window.file_details["channels"].text() == "Stereo"
        window.metadata_fields["title"].setFocus()
        qtbot.keyClicks(window.metadata_fields["title"], "Edited title")
        title = window.track.proposed_metadata.title
        window.grab().save(str(tmp_path / f"details-tab-{variant}.png"))
        window.focus_section("timing")
        assert window.workspace_tabs.currentIndex() == 0
        assert window.lyrics_editor.isVisible()
        assert window.table.hasFocus()
        assert window.lyrics_editor.toPlainText() == "First line\nSecond line"
        assert window.track.aligned_lines == original
        assert window.track.proposed_metadata.title == title
        second = tmp_path / "Second.mp3"
        second.write_bytes(mp3.read_bytes())
        window.load_track(read_track(second))
        assert window.file_details["filename"].text() == "Second.mp3"
        window.song_list.setCurrentRow(0)
        assert window.track.proposed_metadata.title == title
        assert window.track.aligned_lines == original
    finally:
        apply_theme(app)
        window.close()


def test_details_tab_does_not_stamp_or_clear_hidden_timing_rows(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    qtbot.waitUntil(lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)
    window.track.aligned_lines = [LyricLine("Timed", 0), LyricLine("Untimed")]
    window.render_table()
    window.table.selectRow(1)
    window.player.setPosition(1000)
    original = deepcopy(window.track.aligned_lines)
    window.workspace_tabs.setCurrentIndex(1)
    window.activateWindow()
    window.workspace_tabs.tabBar().setFocus()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.keyClick(window.workspace_tabs.tabBar(), Qt.Key.Key_Return)
    qtbot.keyClick(window.workspace_tabs.tabBar(), Qt.Key.Key_Delete)
    assert window.track.aligned_lines == original
    window.table.selectRow(0)
    qtbot.keyClick(window.workspace_tabs.tabBar(), Qt.Key.Key_Delete)
    assert window.track.aligned_lines == original
    window.focus_section("timing")
    window.table.selectRow(1)
    window.player.setPosition(1000)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines[1].start == 1
    window.close()
