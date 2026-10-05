from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtMultimedia import QMediaPlayer
from test_ui import window_for

from song_metadata_enricher.model import LyricLine, Word
from song_metadata_enricher.timing_data import evenly_spaced_words, next_line_start
from song_metadata_enricher.timing_editor import WordTimingDialog


def test_equal_spacing_preserves_words_and_marks_estimates():
    words = evenly_spaced_words("[Verse] One two two don't (x2)", 10, 14, 20)
    assert [word.text for word in words] == ["One", "two", "two", "don't"]
    assert [(word.start, word.end) for word in words] == [(10, 11), (11, 12), (12, 13), (13, 14)]
    assert all(word.source == "estimated" and word.confidence == 0 for word in words)


@pytest.mark.parametrize("start,end", [(1, 1), (2, 1), (-1, 2), (1, 30), (float("nan"), 2)])
def test_equal_spacing_rejects_invalid_bounds(start, end):
    with pytest.raises(ValueError):
        evenly_spaced_words("One two", start, end, 20)


def test_next_line_boundary_uses_audio_order_and_skips_untimed_lines():
    lines = [
        LyricLine("selected", 5),
        LyricLine("missing"),
        LyricLine("reordered", 2),
        LyricLine("next", 9),
        LyricLine("later", 12),
    ]
    assert next_line_start(lines, 0) == 9
    assert next_line_start(lines, 1) is None
    assert next_line_start(lines, 4) is None


def dialog_for(qtbot, mp3, tmp_path, line=None, next_start=2.5):
    window = window_for(qtbot, mp3, tmp_path)
    line = line or LyricLine("One two three four", 0.5, source="manual", confidence=1)
    dialog = WordTimingDialog(line, window.player, 4, window, next_start=next_start)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.graph.width() > 600)
    return window, dialog


def drag(qtbot, dialog, row, mode, delta):
    rect = dialog.graph.bar_rect(row)
    x = (
        rect.left() + 2
        if mode == "start"
        else rect.right() - 2
        if mode == "end"
        else rect.center().x()
    )
    origin = QPoint(round(x), round(rect.center().y()))
    pixels = dialog.graph.time_x(dialog.drafts[row].start + delta) - dialog.graph.time_x(
        dialog.drafts[row].start
    )
    destination = origin + QPoint(round(pixels), 0)
    qtbot.mousePress(dialog.graph, Qt.MouseButton.LeftButton, pos=origin)
    qtbot.mouseMove(dialog.graph, destination)
    qtbot.mouseRelease(dialog.graph, Qt.MouseButton.LeftButton, pos=destination)


def test_automatic_estimates_are_draft_only_and_remain_uncertain_when_applied(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path)
    assert [(word.start, word.end) for word in dialog.drafts] == [
        (0.5, 1),
        (1, 1.5),
        (1.5, 2),
        (2, 2.5),
    ]
    assert not dialog.original.words
    assert not dialog.table.isVisible()
    assert len(dialog.graph.words) == 4
    dialog.apply_words()
    assert dialog.result_line.source == "estimated-words"
    assert dialog.result_line.confidence == 0
    assert all(word.source == "estimated" for word in dialog.result_line.words)
    assert not dialog.original.words
    window.track.aligned_lines = [dialog.result_line]
    window.render_table()
    assert "Estimated" in window.table.item(0, 2).text()
    assert "4 est." in window.table.item(0, 3).text()
    window.close()


def test_drag_shared_edge_resizes_neighbour_and_undo_redo_are_one_step(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path)
    before = deepcopy(dialog.drafts)
    initial_history = len(dialog.history)
    drag(qtbot, dialog, 1, "start", 0.15)
    assert dialog.drafts[1].start == pytest.approx(1.15, abs=0.01)
    assert dialog.drafts[0].end == dialog.drafts[1].start
    assert dialog.drafts[1].end == 1.5
    assert len(dialog.history) == initial_history + 1
    after = deepcopy(dialog.drafts)
    dialog.undo()
    assert dialog.drafts == before
    dialog.redo()
    assert dialog.drafts == after
    dialog.reject()
    window.close()


def test_drag_rectangle_preserves_duration_and_rebalances_neighbours(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path)
    drag(qtbot, dialog, 1, "move", 0.1)
    word = dialog.drafts[1]
    assert word.start == pytest.approx(1.1, abs=0.01)
    assert word.end - word.start == pytest.approx(0.5)
    assert dialog.drafts[0].end == word.start
    assert dialog.drafts[2].start == word.end
    assert word.source == "manual"
    dialog.reject()
    window.close()


def test_graph_drag_clamps_to_bounds_and_neighbours_without_joining(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path)
    dialog.join_check.setChecked(False)
    drag(qtbot, dialog, 1, "end", 0.5)
    assert dialog.drafts[1].end == 1.5
    assert dialog.drafts[2].start == 1.5
    drag(qtbot, dialog, 0, "start", -0.2)
    assert dialog.drafts[0].start == 0.5
    dialog.reject()
    window.close()


def test_ai_words_are_preserved_until_explicit_equal_spacing_and_review(qtbot, mp3, tmp_path):
    line = LyricLine(
        "One two", 0.5, 1.4, 0.6, "ai", [Word("One", 0.5, 0.7, 0.8), Word("two", 1, 1.4, 0.9)]
    )
    window, dialog = dialog_for(qtbot, mp3, tmp_path, line)
    assert [(word.start, word.end) for word in dialog.drafts] == [(0.5, 0.7), (1, 1.4)]
    dialog.space_evenly()
    assert all(word.source == "estimated" for word in dialog.drafts)
    dialog.undo()
    assert [(word.start, word.end) for word in dialog.drafts] == [(0.5, 0.7), (1, 1.4)]
    dialog.redo()
    dialog.review_check.setChecked(True)
    dialog.apply_words()
    assert all(word.source == "estimated" for word in dialog.result_line.words)
    assert dialog.result_line.words_reviewed
    assert dialog.result_line.line_reviewed
    assert dialog.result_line.confidence == 0
    assert line.words[1].start == 1
    window.close()


def test_last_line_uses_audio_end_and_zoom_does_not_change_times(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path, next_start=None)
    assert dialog.drafts[-1].end == 4
    original = deepcopy(dialog.drafts)
    dialog.zoom(1.6)
    assert dialog.graph.minimumWidth() > dialog.scroll.viewport().width()
    assert dialog.drafts == original
    dialog.graph.setMinimumWidth(0)
    dialog.reject()
    window.close()


def test_chart_click_seeks_without_altering_rectangles(qtbot, mp3, tmp_path):
    window, dialog = dialog_for(qtbot, mp3, tmp_path)
    qtbot.waitUntil(lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)
    before = deepcopy(dialog.drafts)
    qtbot.mouseClick(
        dialog.graph, Qt.MouseButton.LeftButton, pos=QPoint(round(dialog.graph.time_x(1.7)), 12)
    )
    assert window.player.position() == pytest.approx(1700, abs=5)
    qtbot.mouseClick(
        dialog.graph, Qt.MouseButton.LeftButton, pos=dialog.graph.bar_rect(2).center().toPoint()
    )
    assert window.player.position() == 1500
    assert dialog.drafts == before
    dialog.reject()
    window.close()


def test_estimated_sources_survive_save_cache_restore_and_json(mp3, tmp_path):
    import json

    from song_metadata_enricher.cache import Cache
    from song_metadata_enricher.tags import read_track, save_track
    from song_metadata_enricher.timing_data import (
        generate_timing_json,
        restore_timing_state,
        store_timing_state,
    )

    track = read_track(mp3)
    track.display_lyrics = "One two\nNext line"
    words = evenly_spaced_words("One two", 0.5, 2.5, track.audio.duration)
    track.aligned_lines = [
        LyricLine("One two", 0.5, 2.5, 0, "estimated-words", words),
        LyricLine("Next line", 2.5),
    ]
    saved = save_track(track)
    cache = Cache(tmp_path / "cache")
    store_timing_state(saved, cache)
    loaded = read_track(mp3)
    assert restore_timing_state(loaded, cache)
    assert loaded.aligned_lines[0].source == "estimated-words"
    assert loaded.aligned_lines[0].confidence == 0
    exported = json.loads(generate_timing_json(loaded))
    assert all(word["source"] == "estimated" for word in exported["lines"][0]["words"])
    assert not loaded.dirty


def test_last_line_spacing_keeps_exact_audio_end(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    duration = 4.0487
    dialog = WordTimingDialog(LyricLine("One two", 0.5), window.player, duration, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.spacing_end == duration
    assert dialog.drafts[-1].end == duration
    dialog.reject()
    window.close()
