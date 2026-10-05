import gc
import weakref

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QMessageBox

from tracksmith.batch import CATEGORIES, timing_category
from tracksmith.config import Settings
from tracksmith.model import LyricLine, Word
from tracksmith.tags import read_track
from tracksmith.ui import MainWindow


def load_category(window, mp3, tmp_path, category, name):
    path = tmp_path / f"{name}.mp3"
    path.write_bytes(mp3.read_bytes())
    track = read_track(path)
    if category:
        track.display_lyrics = "Hello"
        track.aligned_lines = [LyricLine("Hello", None if category == 1 else 0)]
        if category == 3:
            track.aligned_lines[0].words = [Word("Hello", 0, 0.5)]
    track.mark_saved()
    window.load_track(track)
    assert timing_category(track) == CATEGORIES[category]
    return track


@pytest.mark.parametrize("category", range(4))
@pytest.mark.parametrize("remove_current", [False, True])
def test_unload_entire_category_keeps_other_songs(qtbot, mp3, tmp_path, category, remove_current):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    first = load_category(window, mp3, tmp_path, category, "first")
    hidden = load_category(window, mp3, tmp_path, category, "hidden")
    keep = load_category(window, mp3, tmp_path, (category + 1) % 4, "keep")
    keep_session = window.current_session()
    window.metadata_fields["title"].setText("Keep my draft")
    window.metadata_edited()
    if remove_current:
        window.song_list.setCurrentRow(0)
    window.category_filter.setCurrentIndex(category + 1)
    window.song_search.setText("first")
    assert window.song_list.item(1).isHidden()
    originals = {track.path: track.path.read_bytes() for track in (first, hidden, keep)}
    qtbot.mouseClick(window.unload_category_button, Qt.MouseButton.LeftButton)
    assert window.sessions == [keep_session]
    assert window.track is keep
    assert window.metadata_fields["title"].text() == "Keep my draft"
    assert window.song_list.count() == 1
    assert window.category_filter.currentIndex() == 0
    assert window.category_filter.itemText(category + 1).endswith("(0)")
    assert not window.unload_category_button.isEnabled()
    assert all(path.read_bytes() == data for path, data in originals.items())
    recovery = window.workspace_store.read(window.workspace_store.drafts_path)
    assert str(first.path) not in str(recovery)
    assert str(hidden.path) not in str(recovery)
    window.close()


def test_unload_unsaved_category_cancel_then_discard_releases_state(
    qtbot, mp3, tmp_path, monkeypatch
):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    first = load_category(window, mp3, tmp_path, 0, "first")
    window.lyrics_editor.setPlainText("Pending unsaved lyrics")
    window.record_change("edit lyrics")
    window.remember_current_song()
    session_ref = weakref.ref(window.current_session())
    track_ref = weakref.ref(first)
    original = first.path.read_bytes()
    path = first.path
    window.category_filter.setCurrentIndex(1)
    prompts = []

    def cancel(*args):
        prompts.append(args)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", cancel)
    window.unload_category()
    assert window.track is first
    assert len(window.sessions) == 1
    assert window._lyrics_pending
    assert "1 song(s) have unsaved edits" in prompts[0][2]
    assert prompts[0][-1] == QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    window.waveform.set_audio(first.audio.duration, [0.5] * 100)
    window.unload_category()
    del first
    gc.collect()
    assert session_ref() is None
    assert track_ref() is None
    assert not window.sessions
    assert window.track is None
    assert window.player.source().isEmpty()
    assert not window.waveform.peaks
    assert not window._stamp_history
    assert window._edit_baseline is None
    assert not window.release_candidates
    assert not window.lyrics_editor.toPlainText()
    assert window.table.rowCount() == 0
    assert window.workspace_stack.currentIndex() == 0
    assert path.read_bytes() == original
    window.close()


def test_unload_disabled_for_all_empty_and_busy_categories(qtbot, mp3, tmp_path):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    load_category(window, mp3, tmp_path, 0, "first")
    window.unload_category()
    assert len(window.sessions) == 1
    assert not window.unload_category_button.isEnabled()
    window.category_filter.setCurrentIndex(2)
    assert not window.unload_category_button.isEnabled()
    window.category_filter.setCurrentIndex(1)
    assert window.unload_category_button.isEnabled()
    window.job = object()
    window._enabled()
    assert not window.unload_category_button.isEnabled()
    window.unload_category()
    assert len(window.sessions) == 1
    window.job = None
    window.close()


@pytest.mark.parametrize("discard", [False, True])
def test_context_menu_unloads_clicked_song_with_unsaved_prompt(
    qtbot, mp3, tmp_path, monkeypatch, discard
):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    clicked = load_category(window, mp3, tmp_path, 0, "clicked")
    window.lyrics_editor.setPlainText("Unsaved lyrics")
    window.record_change("edit lyrics")
    keep = load_category(window, mp3, tmp_path, 0, "keep")
    assert window.track is keep
    original = clicked.path.read_bytes()

    class ChoosingMenu(QMenu):
        def exec(self, position):
            action = self.actions()[0]
            assert action.text() == "Unload song"
            assert action.isEnabled()
            return action

    monkeypatch.setattr("tracksmith.ui.QMenu", ChoosingMenu)
    prompts = []

    def answer(*args):
        prompts.append(args[2])
        return QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", answer)
    position = window.song_list.visualItemRect(window.song_list.item(0)).center()
    window.song_context_menu(position)
    assert clicked.path.name in prompts[0]
    assert [s.track for s in window.sessions] == ([keep] if discard else [clicked, keep])
    assert clicked.path.read_bytes() == original
    window.close()
