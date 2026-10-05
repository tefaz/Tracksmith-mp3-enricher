from copy import deepcopy
from shutil import copyfile
from threading import Event

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog

from tracksmith.batch import (
    CATEGORIES,
    best_lyrics,
    folder_mp3s,
    run_batch,
    timing_category,
)
from tracksmith.config import Settings
from tracksmith.jobs import JobContext
from tracksmith.model import LyricLine, Word
from tracksmith.providers import LyricsCandidate
from tracksmith.tags import read_track, save_track
from tracksmith.ui import MainWindow


def test_folder_progress_is_overall_and_elapsed_keeps_ticking(qtbot, mp3, tmp_path, monkeypatch):
    import tracksmith.ui as ui

    copyfile(mp3, tmp_path / "second.mp3")

    class RecordingWindow(MainWindow):
        @Slot(str, int)
        def job_progress(self, text, percent):
            progress.append(percent)
            super().job_progress(text, percent)

    progress = []
    window = RecordingWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    entered, release = Event(), Event()
    displayed = []
    original_display = window.display_song

    def display(index):
        displayed.append(index)
        original_display(index)

    def read(path, context):
        context.progress("Read MP3", 10)
        entered.set()
        assert release.wait(5)
        return read_track(path, context)

    monkeypatch.setattr(window, "display_song", display)
    monkeypatch.setattr(ui, "read_track", read)
    window.open_folder(tmp_path)
    try:
        qtbot.waitUntil(entered.is_set)
        window.job_started -= 3
        qtbot.waitUntil(lambda: "Elapsed 00:03" in window.job_activity_label.text())
        before = window.job_activity_label.text()
        window.job_started -= 3
        qtbot.waitUntil(lambda: window.job_activity_label.text() != before)
        assert window.job is not None
    finally:
        release.set()
    qtbot.waitUntil(lambda: window.job is None and len(window.sessions) == 2)
    assert progress[0] == -1  # Animated busy indicator while discovering files.
    determinate = [value for value in progress if value >= 0]
    assert determinate == sorted(determinate)
    assert any(40 <= value <= 60 for value in determinate)
    assert displayed == [0]
    assert window.progress.value() == 100
    assert "Loaded 2 new songs" in window.job_heading.text()
    window.close()


class Provider:
    def search(self, artist, title, context):
        return [
            LyricsCandidate(title, "Other artist", "", "Wrong lyrics"),
            LyricsCandidate(title, artist, "", "First line\nSecond line"),
        ]


def test_categories_and_matching(mp3):
    track = read_track(mp3)
    assert timing_category(track) == CATEGORIES[0]
    track.display_lyrics = "A word\nB"
    track.aligned_lines = [LyricLine("A word", 0), LyricLine("B")]
    # Draft text is not embedded yet.
    assert timing_category(track) == CATEGORIES[0]
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[1]
    track.aligned_lines[1].excluded = True
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[2]
    track.aligned_lines[0].words = [Word("A", 0, 0.3)]
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[2]
    track.aligned_lines[0].words.append(Word("word", 0.3, 0.6))
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[3]
    track.aligned_lines[1].excluded = False
    track.aligned_lines[1].start = 2
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[2]
    track.aligned_lines[1].words = [Word("B", 2, 2.3)]
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[3]
    track.aligned_lines[1].words[0].end = 2
    track.mark_saved()
    assert timing_category(track) == CATEGORIES[2]
    candidates = Provider().search("Example Artist", "Example Song", JobContext())
    assert best_lyrics(track, candidates).lyrics.startswith("First")
    assert best_lyrics(track, candidates[:1]) is None


def test_batch_embeds_lyrics_and_timings(mp3, monkeypatch):
    track = read_track(mp3)
    result = run_batch([track], "lyrics", Settings(), Provider(), JobContext())
    assert len(result.saved) == 1
    saved = read_track(mp3)
    assert saved.display_lyrics == "First line\nSecond line"
    assert timing_category(saved) == CATEGORIES[1]
    monkeypatch.setattr(
        "tracksmith.batch.run_alignment",
        lambda *args: [LyricLine("First line", 0.3), LyricLine("Second line")],
    )
    result = run_batch([saved], "timing", Settings(), None, JobContext())
    assert len(result.saved) == 1
    assert timing_category(read_track(mp3)) == CATEGORIES[1]
    assert not result.saved[0].dirty
    assert not result.saved[0].aligned_lines[0].line_reviewed
    # A second timing pass cannot replace partial timing.
    result = run_batch(result.saved, "timing", Settings(), None, JobContext())
    assert not result.saved
    assert "already has line timings" in result.rows[0][1]


def test_batch_preserves_edits_and_existing_timing(mp3):
    track = read_track(mp3)
    dirty = deepcopy(track)
    dirty.proposed_metadata.title = "Unsaved title"
    assert not run_batch([dirty], "lyrics", Settings(), Provider(), JobContext()).saved
    track.display_lyrics = "My timed line"
    track.aligned_lines = [LyricLine("My timed line", 1)]
    track = save_track(track)
    result = run_batch([track], "lyrics", Settings(), Provider(), JobContext(), replace=True)
    assert not result.saved
    assert "would remove" in result.rows[0][1]
    assert read_track(mp3).aligned_lines[0].start == 1


def test_cancel_keeps_commits_and_failure_continues(mp3, tmp_path):
    second = tmp_path / "Example Artist - Example Song 2.mp3"
    copyfile(mp3, second)
    tracks = [read_track(mp3), read_track(second)]
    context = JobContext()
    result = run_batch(
        tracks,
        "lyrics",
        Settings(),
        Provider(),
        context,
        on_saved=lambda track: context.cancelled.set(),
    )
    assert result.cancelled and len(result.saved) == 1
    assert read_track(mp3).display_lyrics
    assert not read_track(second).display_lyrics
    missing = deepcopy(tracks[0])
    missing.path = tmp_path / "absent" / mp3.name
    result = run_batch([missing, tracks[1]], "lyrics", Settings(), Provider(), JobContext())
    assert len(result.saved) == 1
    assert result.rows[0][1].startswith("Failed")


def test_folder_and_ui_batch(qtbot, mp3, tmp_path, monkeypatch):
    upper = tmp_path / "Example Artist - Other Song.MP3"
    copyfile(mp3, upper)
    nested = tmp_path / "nested"
    nested.mkdir()
    copyfile(mp3, nested / "nested.mp3")
    assert len(folder_mp3s(tmp_path)) == 2
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.open_folder(tmp_path)
    qtbot.waitUntil(lambda: len(window.sessions) == 2 and window.job is None, timeout=10000)
    assert window.category_filter.count() == 5
    window.category_filter.setCurrentIndex(1)
    assert all(not window.song_list.item(i).isHidden() for i in range(2))
    monkeypatch.setattr("tracksmith.ui.LRCLibProvider", lambda http: Provider())
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.start_batch("lyrics")
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert all(session.track.display_lyrics for session in window.sessions)
    assert all(not session.track.dirty for session in window.sessions)
    window.category_filter.setCurrentIndex(2)
    monkeypatch.setattr(
        "tracksmith.batch.run_alignment",
        lambda *args: [LyricLine("First line", 0.3), LyricLine("Second line")],
    )
    window.start_batch("timing")
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert all(timing_category(session.track) == CATEGORIES[1] for session in window.sessions)
    assert all(
        timing_category(read_track(session.track.path)) == CATEGORIES[1]
        for session in window.sessions
    )
    window.category_filter.setCurrentIndex(4)
    assert all(window.song_list.item(i).isHidden() for i in range(2))
    window.close()


def test_empty_folder(qtbot, tmp_path):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.open_folder(tmp_path)
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert not window.sessions
    window.close()
