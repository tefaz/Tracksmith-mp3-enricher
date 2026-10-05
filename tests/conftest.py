import os
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

import pytest


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path, qtbot):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from song_metadata_enricher.timing_editor import WordTimingDialog
    from song_metadata_enricher.ui import MainWindow

    monkeypatch.setattr(WordTimingDialog, "confirm_discard", lambda self: True)
    original_init = MainWindow.__init__

    def test_window_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.confirm_discard = lambda: True

    monkeypatch.setattr(MainWindow, "__init__", test_window_init)
    yield
    from song_metadata_enricher.ui import MainWindow

    monkeypatch.setattr(MainWindow, "confirm_discard", lambda self: True)


@pytest.fixture
def mp3(tmp_path: Path) -> Path:
    """Generated tone: no commercial audio fixtures."""
    path = tmp_path / "Example Artist - Example Song edited.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(path),
        ],
        check=True,
    )
    return path
