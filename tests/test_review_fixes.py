import shutil
from collections import Counter

from tracksmith import providers
from tracksmith.alignment import withhold_crossing_lines
from tracksmith.config import Settings
from tracksmith.embedded_timing import sylt_cues
from tracksmith.model import LyricLine, Track, Word
from tracksmith.ui import PENDING_LYRICS_MESSAGE, MainWindow


def two_song_window(qtbot, mp3, tmp_path):
    other = tmp_path / "Other Artist - Other Song.mp3"
    shutil.copy(mp3, other)
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.open_paths([mp3, other])
    qtbot.waitUntil(lambda: window.job is None and len(window.sessions) == 2)
    return window


def test_song_list_rechecks_only_songs_that_can_have_changed(qtbot, mp3, tmp_path, monkeypatch):
    window = two_song_window(qtbot, mp3, tmp_path)
    first, second = (session.track for session in window.sessions)
    assert window.track is first
    first.proposed_metadata.title = "Edited title"
    window._title()
    assert window.song_list.item(0).text().startswith("• ")

    calls = Counter()
    original = Track.editable_state
    monkeypatch.setattr(
        Track, "editable_state", lambda self: calls.update([id(self)]) or original(self)
    )
    window._title()
    # The other, untouched song is not compared with its saved state again.
    assert calls[id(second)] == 0 and calls[id(first)] > 0

    window.song_list.setCurrentRow(1)
    qtbot.waitUntil(lambda: window.track is second)
    window._title()
    calls.clear()
    window._title()
    assert calls[id(first)] == 0
    # The previously open song keeps its unsaved marker after switching away.
    assert window.song_list.item(0).text().startswith("• ")
    assert not window.song_list.item(1).text().startswith("• ")


def test_stamping_with_unapplied_lyrics_explains_why_nothing_happened(qtbot, mp3, tmp_path):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.open_paths([mp3])
    qtbot.waitUntil(lambda: window.track is not None and window.job is None)
    window._lyrics_pending = True
    window.stamp_line()
    assert window.statusBar().currentMessage() == PENDING_LYRICS_MESSAGE
    window.statusBar().clearMessage()
    window.edit_word_timings()
    assert window.statusBar().currentMessage() == PENDING_LYRICS_MESSAGE


def test_lines_that_cut_into_each_other_are_withheld_so_saving_still_works():
    def line(text, start, step, confidence):
        words = [
            Word(token, start + index * step, start + (index + 1) * step, confidence)
            for index, token in enumerate(text.split())
        ]
        return LyricLine(text, words[0].start, words[-1].end, confidence, "ai", words)

    first = line("one two three", 1.0, 0.4, 0.9)  # last word starts at 1.8
    second = line("four five", 1.7, 0.3, 0.6)  # starts before that word
    third = line("six seven", 3.0, 0.3, 0.8)
    lines = withhold_crossing_lines([first, second, third])
    assert second.start is None and second.words == [] and "withheld" in second.note
    assert first.start == 1.0 and third.start == 3.0
    sylt_cues(lines)  # Raised "Word timings cross the following line" before.


def test_online_requests_identify_the_app_without_personal_details():
    agent = providers.USER_AGENT
    assert agent.startswith(f"Tracksmith/{providers.__version__} ")
    assert "http" not in agent and "@" not in agent and "github" not in agent.lower()
