import json
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QDialog, QFileDialog
from test_ui import window_for

from tracksmith.dialogs import SaveReviewDialog
from tracksmith.model import LyricLine, Word
from tracksmith.tags import read_track
from tracksmith.theme import timing_selection_color
from tracksmith.timing_data import generate_timing_json
from tracksmith.timing_editor import WordTimingDialog


def ready_player(qtbot, window):
    qtbot.waitUntil(lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)


def test_main_window_buttons_apply_repairs_and_word_edits(qtbot, mp3, tmp_path, monkeypatch):
    window = window_for(qtbot, mp3, tmp_path)
    ready_player(qtbot, window)
    window.lyrics_editor.setPlainText("Hello world\nMissing line")
    window.apply_lyrics()
    window.track.aligned_lines[0] = LyricLine(
        "Hello world",
        0.5,
        1.4,
        0.9,
        "ai",
        [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
    )
    window.render_table()

    window.table.selectRow(1)
    window.player.setPosition(2000)
    window.stamp_button.click()
    assert window.track.aligned_lines[1].start == 2
    assert "2 / 2 lines timed" in window.timing_summary.text()
    window.table.selectRow(0)

    def edit_words(dialog):
        dialog.table.item(0, 1).setText("00:00.550")
        dialog.apply_words()
        return dialog.result()

    monkeypatch.setattr(WordTimingDialog, "exec", edit_words)
    window.words_button.click()
    assert window.track.aligned_lines[0].words[0].start == 0.55
    assert window.track.aligned_lines[0].start == 0.55
    assert window.track.aligned_lines[0].source == "manual-words"
    # A line correction keeps and shifts the word detail.
    window.table.item(0, 0).setText("00:00.650")
    assert window.track.aligned_lines[0].words[0].start == pytest.approx(0.65)
    assert window.track.aligned_lines[0].words[1].start == pytest.approx(1.1)
    assert "2 words timed" in window.timing_summary.text()
    assert not read_track(mp3).aligned_lines  # Repairs are still in memory.
    window.close()


def test_word_view_highlights_without_modifying_data_and_cancel_discards_draft(
    qtbot, mp3, tmp_path
):
    window = window_for(qtbot, mp3, tmp_path)
    line = LyricLine(
        "Hello world",
        0.5,
        1.4,
        0.65,
        "ai",
        [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
    )
    before = deepcopy(line)
    dialog = WordTimingDialog(line, window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table.item(0, 1).text() == "00:00.500"
    dialog.playback_position(1100)
    assert dialog.table.item(1, 0).background().color().name() == timing_selection_color(dialog.table).name()
    assert line == before
    dialog.table.item(0, 1).setText("00:00.550")
    assert dialog.drafts[0].start == 0.55
    assert dialog.drafts[0].confidence == 0.7  # Editing only the start leaves end support intact.
    dialog.reject()
    assert line == before
    assert dialog.result_line is None
    window.close()


def test_manual_word_start_end_shortcuts_derive_line_and_retain_word_times(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    ready_player(qtbot, window)
    line = LyricLine("Hello world")
    dialog = WordTimingDialog(line, window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    dialog.table.setFocus()
    qtbot.waitUntil(lambda: dialog.isActiveWindow())
    assert all(word.start is None and word.end is None for word in dialog.drafts)
    for start, end in ((500, 800), (1000, 1400)):
        window.player.setPosition(start)
        qtbot.keyClick(dialog.table, Qt.Key.Key_Return)
        window.player.setPosition(end)
        qtbot.keyClick(dialog.table, Qt.Key.Key_Return, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert dialog.table.currentRow() == 1
    dialog.apply_words()
    result = dialog.result_line
    assert result.start == 0.5 and result.end == 1.4
    assert [(word.start, word.end) for word in result.words] == [(0.5, 0.8), (1, 1.4)]
    assert result.confidence == 1
    assert line.start is None and not line.words
    window.close()


def test_word_editor_rejects_incomplete_invalid_and_overlapping_spans(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    dialog = WordTimingDialog(LyricLine("Hello world"), window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.table.item(0, 1).setText("00:00.500")
    dialog.apply_words()
    assert dialog.result_line is None
    assert "Word 1" in dialog.message.text() and "start and an end" in dialog.message.text()
    dialog.table.item(0, 2).setText("00:00.400")
    dialog.table.item(1, 1).setText("00:00.600")
    dialog.table.item(1, 2).setText("00:00.900")
    dialog.apply_words()
    assert "start before its end" in dialog.message.text()
    dialog.table.item(0, 2).setText("00:00.700")
    dialog.apply_words()
    assert "Word 2" in dialog.message.text() and "before word 1 ends" in dialog.message.text()
    dialog.table.item(1, 1).setText("00:05.000")
    assert "inside the audio" in dialog.message.text()
    assert dialog.drafts[1].start == 0.6
    dialog.reject()
    window.close()


def test_word_edits_do_not_raise_confidence_of_untouched_ai_words(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    line = LyricLine(
        "Hello world",
        0.5,
        1.4,
        0.6,
        "ai",
        [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
    )
    dialog = WordTimingDialog(line, window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.table.item(0, 1).setText("00:00.550")
    dialog.table.item(0, 2).setText("00:00.850")
    dialog.apply_words()
    assert dialog.result_line.confidence == 0.6
    assert dialog.result_line.words[1].confidence == 0.9
    window.close()


def test_moving_line_preserves_words_and_rejects_out_of_bounds_atomically():
    line = LyricLine(
        "Hello world",
        0.5,
        1.4,
        0.9,
        "ai",
        [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
    )
    line.move_timestamp(0.7, 4)
    assert [value for word in line.words for value in (word.start, word.end)] == pytest.approx(
        [0.7, 1, 1.2, 1.6]
    )
    before = deepcopy(line)
    with pytest.raises(ValueError, match="outside the audio"):
        line.move_timestamp(3.9, 4)
    assert line == before
    line.move_timestamp(None, 4)
    assert not line.words and line.start is None


def test_word_data_export_and_mp3_save_keep_in_memory_words(qtbot, mp3, tmp_path, monkeypatch):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("Hello world\nMissing line")
    window.apply_lyrics()
    line = LyricLine(
        "Hello world",
        0.5,
        1.4,
        0.9,
        "ai",
        [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
    )
    window.track.aligned_lines[0] = line
    window.render_table()
    assert "2 words timed" in window.timing_summary.text()
    assert window.table.item(0, 3).text() == "2 timed"
    data = json.loads(generate_timing_json(window.track))
    assert data["lines"][0]["words"][1]["end"] == 1.4
    assert data["lines"][1]["start"] is None
    destination = tmp_path / "song.timings.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    window.export_word_timings()
    assert json.loads(destination.read_text()) == data
    monkeypatch.setattr(SaveReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    window.save()
    qtbot.waitUntil(lambda: window.job is None)
    assert window.track.aligned_lines[0].words == line.words
    assert not window.track.dirty
    # Both standard word starts and exact editing data travel inside the MP3.
    assert read_track(mp3).aligned_lines[0].start == 0.5
    assert read_track(mp3).aligned_lines[0].words == line.words
    assert len(read_track(mp3).aligned_lines) == 2
    reopened = window_for(qtbot, mp3, tmp_path)
    assert reopened.track.aligned_lines[0].words == line.words
    assert reopened.track.aligned_lines[1].start is None
    assert not reopened.track.dirty
    reopened.close()
    window.close()


def test_saved_timing_cache_rejects_changes_and_bad_word_spans(mp3, tmp_path):
    from tracksmith.cache import Cache
    from tracksmith.tags import save_track
    from tracksmith.timing_data import restore_timing_state, store_timing_state

    track = read_track(mp3)
    track.display_lyrics = "Hello world\nMissing"
    track.aligned_lines = [
        LyricLine(
            "Hello world",
            0.5,
            1.4,
            0.9,
            "ai",
            [Word("Hello", 0.5, 0.8, 0.7), Word("world", 1, 1.4, 0.9)],
        ),
        LyricLine("Missing"),
    ]
    saved = save_track(track)
    cache = Cache(tmp_path / "cache")
    store_timing_state(saved, cache)
    loaded = read_track(mp3)
    loaded.content_hash = "different bytes"
    assert not restore_timing_state(loaded, cache)
    loaded = read_track(mp3)
    loaded.display_lyrics = "edited"
    assert not restore_timing_state(loaded, cache)
    loaded = read_track(mp3)
    loaded.aligned_lines[0].start = 0.7
    assert not restore_timing_state(loaded, cache)
    saved.aligned_lines[0].words[0].end = 100
    store_timing_state(saved, cache)
    assert not restore_timing_state(read_track(mp3), cache)


def test_saved_word_corrections_survive_cache_write_failure(qtbot, mp3, tmp_path, monkeypatch):
    import tracksmith.ui as ui
    from tracksmith.cache import Cache
    from tracksmith.timing_data import store_timing_state

    window = window_for(qtbot, mp3, tmp_path)
    window.track.display_lyrics = "Hello"
    window.track.aligned_lines = [
        LyricLine("Hello", 0.5, 0.8, 0.9, "ai", [Word("Hello", 0.5, 0.8, 0.9)])
    ]
    store_timing_state(window.track, Cache(tmp_path / "cache"))
    corrected = deepcopy(window.track)
    corrected.aligned_lines[0].words[0].end = 1
    corrected.aligned_lines[0].end = 1
    corrected.status = "Saved"
    corrected.mark_saved()

    def cannot_write(*args):
        raise OSError("Cache directory is not writable")

    monkeypatch.setattr(ui, "store_timing_state", cannot_write)
    window.saved(corrected)
    assert window.track.aligned_lines[0].words[0].end == 1
    assert not window.track.dirty
    assert "MP3 saved" in window.statusBar().currentMessage()
    assert "cache could not be written" in window.statusBar().currentMessage()
    window.close()


def test_clear_automatic_words_keeps_line_times_manual_edits_and_supports_undo(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.track.aligned_lines = [
        LyricLine("AI words", 0.2, 0.8, 0.9, "ai", [Word("AI", 0.2, 0.4), Word("words", 0.4, 0.8)], line_reviewed=True),
        LyricLine("Manual words", 1, 1.8, words=[Word("Manual", 1, 1.3, 1, "manual"), Word("words", 1.3, 1.8)]),
        LyricLine("Estimate", 2, 2.5, words=[Word("Estimate", 2, 2.5, 0, "estimated")]),
        LyricLine("Adjusted", 3, 3.5, words=[Word("Adjusted", 3, 3.5, 0.8, "mixed")]),
    ]
    window.render_table()
    original = deepcopy(window.track.aligned_lines)
    assert window.clear_auto_words_button.isEnabled()
    window.clear_auto_words_button.click()
    assert not window.track.aligned_lines[0].words
    assert not window.track.aligned_lines[2].words
    assert window.track.aligned_lines[1] == original[1]
    assert window.track.aligned_lines[3] == original[3]
    for line, old in zip(window.track.aligned_lines, original):
        assert (line.start, line.end, line.source, line.confidence, line.line_reviewed) == (old.start, old.end, old.source, old.confidence, old.line_reviewed)
    assert not window.clear_auto_words_button.isEnabled()
    window.undo_stamp()
    assert window.track.aligned_lines == original
    assert window.clear_auto_words_button.isEnabled()
    window.close()


def test_clear_all_timings_preserves_lyrics_and_exclusions_and_undoes_in_one_step(
    qtbot, mp3, tmp_path
):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("AI\nManual\nEstimate\nLine only\nNot sung")
    window.apply_lyrics()
    window.track.aligned_lines = [
        LyricLine("AI", 0.2, 0.8, 0.9, "ai", [Word("AI", 0.2, 0.8)]),
        LyricLine(
            "Manual", 1, 1.8, words=[Word("Manual", 1, 1.8, 1, "manual")],
            line_reviewed=True, words_reviewed=True,
        ),
        LyricLine("Estimate", 2, 2.5, words=[Word("Estimate", 2, 2.5, 0, "estimated")]),
        LyricLine("Line only", 3, source="manual", line_reviewed=True),
        LyricLine("Not sung", excluded=True, note="Different version"),
    ]
    window.track.mark_saved()
    window.render_table()
    original = deepcopy(window.track.aligned_lines)
    lyrics = window.track.display_lyrics
    history_size = len(window.current_session().history)
    window.table.clearSelection()
    window.table.setCurrentCell(-1, -1)
    assert window.clear_all_times_button.isEnabled()
    window.clear_all_times_button.click()
    assert window.track.dirty
    assert window.track.display_lyrics == lyrics
    assert [line.line_id for line in window.track.aligned_lines] == [
        line.line_id for line in original
    ]
    assert [line.text for line in window.track.aligned_lines] == [line.text for line in original]
    assert all(
        line.start is None and line.end is None and not line.words
        for line in window.track.aligned_lines
    )
    assert all(
        not line.line_reviewed and not line.words_reviewed
        for line in window.track.aligned_lines[:4]
    )
    assert window.track.aligned_lines[-1] == original[-1]
    assert not window.clear_all_times_button.isEnabled()
    assert len(window.current_session().history) == history_size + 1
    window.clear_all_timings()
    assert len(window.current_session().history) == history_size + 1
    window.undo_stamp()
    assert window.track.aligned_lines == original
    assert not window.track.dirty
    assert window.clear_all_times_button.isEnabled()
    window.redo_edit()
    assert all(line.start is None and not line.words for line in window.track.aligned_lines)
    window.close()


def test_clear_all_timings_requires_idle_paused_applied_lyrics(qtbot, mp3, tmp_path, monkeypatch):
    window = window_for(qtbot, mp3, tmp_path)
    assert not window.clear_all_times_button.isEnabled()
    window.track.aligned_lines = [LyricLine("Timed", 1)]
    window.render_table()
    original = deepcopy(window.track.aligned_lines)
    monkeypatch.setattr(window, "is_playing", lambda: True)
    window.update_timing_selection()
    assert not window.clear_all_times_button.isEnabled()
    window.clear_all_timings()
    assert window.track.aligned_lines == original
    monkeypatch.setattr(window, "is_playing", lambda: False)
    window.job = object()
    window.update_timing_selection()
    assert not window.clear_all_times_button.isEnabled()
    window.clear_all_timings()
    assert window.track.aligned_lines == original
    window.job = None
    window.lyrics_editor.setPlainText("Pending edit")
    assert not window.clear_all_times_button.isEnabled()
    window.clear_all_timings()
    assert window.track.aligned_lines == original
    window.close()
