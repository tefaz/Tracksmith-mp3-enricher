from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from tracksmith.config import Settings
from tracksmith.tags import read_track
from tracksmith.ui import MainWindow


@pytest.mark.parametrize("legacy", ["missing", "malformed", "oversized"])
def test_edits_stay_in_memory_and_next_launch_keeps_only_preferences(
    qtbot, mp3, tmp_path, monkeypatch, legacy
):
    directory = tmp_path / "data"
    directory.mkdir()
    old_recovery = directory / "recovery.json"
    if legacy != "missing":
        with old_recovery.open("wb") as stream:
            stream.write(b"invalid old workspace")
            if legacy == "oversized":
                stream.truncate(64 * 1024 * 1024 + 1)
        original = old_recovery.stat()
    original_open = Path.open

    def open_without_recovery(path, *args, **kwargs):
        assert path != old_recovery, "Old workspace must never be read or written"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_without_recovery)
    settings = Settings(cache_directory=str(tmp_path / "cache"), workspace_directory=str(directory))
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert not window.sessions
    menu_labels = {
        action.text() for menu in window.menuBar().actions() for action in menu.menu().actions()
    }
    assert not menu_labels & {
        "Restore previous workspace",
        "Recovery status",
        "Discard retained recovery",
    }
    assert all(
        "recovery" not in label.text().lower()
        for label in window.activity_dialog.findChildren(QLabel)
    )
    mp3_before = mp3.read_bytes()
    window.load_track(read_track(mp3))
    original_title = window.track.proposed_metadata.title
    window.metadata_fields["title"].setText("Unsaved title")
    window.metadata_edited()
    window.undo_stamp()
    assert window.track.proposed_metadata.title == original_title
    window.redo_edit()
    assert window.track.proposed_metadata.title == "Unsaved title"
    window.lyrics_editor.setPlainText("Unsaved lyrics")
    window.volume.setValue(42)
    qtbot.wait(800)
    window.close()
    assert mp3.read_bytes() == mp3_before
    reopened = MainWindow(settings)
    qtbot.addWidget(reopened)
    assert not reopened.sessions and reopened.track is None
    assert reopened.volume.value() == 42
    reopened.close()
    if legacy == "missing":
        assert not old_recovery.exists()
    else:
        assert old_recovery.stat().st_size == original.st_size
        assert old_recovery.stat().st_mtime_ns == original.st_mtime_ns
