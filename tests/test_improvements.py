"""Regression coverage for the UI audit and durable editing workflows."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from test_ui import window_for

from tracksmith.cache import Cache, clear_disposable, disposable_inventory, file_hash
from tracksmith.config import Settings
from tracksmith.dialogs import MetadataProposalDialog, SaveReviewDialog
from tracksmith.jobs import Cancelled, JobContext
from tracksmith.lyrics import generate_lrc, parse_lrc
from tracksmith.model import LyricLine, Metadata, Word, needs_review, review_summary
from tracksmith.projects import read_project
from tracksmith.tags import read_track
from tracksmith.theme import apply_theme
from tracksmith.timing_data import (
    generate_timing_json,
    parse_timing_project,
    validate_lines,
)
from tracksmith.timing_editor import WordTimingDialog
from tracksmith.ui import MainWindow, SettingsDialog
from tracksmith.waveform import decode_overview


def ready(qtbot, window):
    qtbot.waitUntil(lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)


def timed_window(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.track.display_lyrics = "Before\nMissing\nAfter"
    window.track.aligned_lines = [
        LyricLine("Before", 0.5, 0.9, 0.4, "ai", [Word("Before", 0.5, 0.9, 0.4)]),
        LyricLine("Missing"),
        LyricLine("After", 2),
    ]
    window._rendering = True
    window.lyrics_editor.setPlainText(window.track.display_lyrics)
    window._rendering = False
    window.render_table()
    ready(qtbot, window)
    return window


def test_focused_slider_and_sidebar_do_not_edit_timestamps(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    window.table.selectRow(0)
    original = deepcopy(window.track.aligned_lines)
    window.timeline.setFocus()
    qtbot.keyClick(window.timeline, Qt.Key.Key_Right)
    assert window.track.aligned_lines == original
    assert window.player.position() > 0
    second = tmp_path / "second.mp3"
    second.write_bytes(mp3.read_bytes())
    first = window.track
    window.load_track(read_track(second))
    window.song_list.setCurrentRow(0)
    window.song_list.setFocus()
    qtbot.keyClick(window.song_list, Qt.Key.Key_Down)
    assert window.track.path == second
    assert first.aligned_lines == original


def test_enter_on_focused_button_stamps_instead_of_activating_it(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.activateWindow()
    window.table.selectRow(1)
    window.player.setPosition(1100)
    window.apply_button.setFocus()
    clicks = []
    window.apply_button.clicked.connect(lambda: clicks.append(True))
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.keyClick(window.apply_button, Qt.Key.Key_Return)
    assert not clicks
    assert window.track.aligned_lines[1].start == 1.1


def test_exclusions_reviews_and_support_remain_independent(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    window._set_selected(0.6)
    line = window.track.aligned_lines[0]
    assert line.line_reviewed and not line.words_reviewed
    assert line.words[0].confidence == 0.4 and needs_review(line)
    window.review_decision("words")
    assert not needs_review(line)
    assert line.words[0].confidence == 0.4 and line.words[0].source == "ai"
    window.table.selectRow(1)
    window.review_decision("excluded")
    assert window.track.aligned_lines[1].excluded
    assert review_summary(window.track.aligned_lines)["missing"] == 0
    assert "Missing" not in generate_lrc(window.track.aligned_lines)
    assert "Missing" in window.track.display_lyrics
    window.undo_stamp()
    assert not window.track.aligned_lines[1].excluded


def test_general_undo_restores_words_and_is_per_song(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    before = deepcopy(window.track.aligned_lines[0])
    window.unmatch_line()
    assert not window.track.aligned_lines[0].words
    window.undo_stamp()
    assert window.track.aligned_lines[0] == before
    window.redo_edit()
    assert window.track.aligned_lines[0].start is None
    second = tmp_path / "another.mp3"
    second.write_bytes(mp3.read_bytes())
    window.load_track(read_track(second))
    assert not window.current_session().history
    window.song_list.setCurrentRow(0)
    window.undo_stamp()
    assert window.track.aligned_lines[0] == before


def test_pending_lyrics_lock_the_old_table_without_losing_text(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("[Verse]\nUpdated before\nMissing\nAfter")
    text = window.track.display_lyrics
    assert window.pending_banner.isVisible()
    assert not window.table.isEnabled()
    window._set_selected(1)
    assert window.track.aligned_lines[0].start == 0.5
    window.table.item(0, 1).setText("Stale table edit")
    assert window.track.display_lyrics == text


def test_project_roundtrip_validation_and_legacy_migration(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.track.aligned_lines[1].excluded = True
    data = json.loads(generate_timing_json(window.track))
    parsed = parse_timing_project(json.dumps(data), window.track)
    assert parsed["lines"] == window.track.aligned_lines
    data["source"]["content_sha256"] = "wrong"
    with pytest.raises(ValueError, match="different or changed"):
        parse_timing_project(json.dumps(data), window.track)
    legacy = asdict(LyricLine("Manual", 0.5, source="manual"))
    for key in ("line_id", "line_reviewed", "words_reviewed", "excluded"):
        legacy.pop(key)
    assert validate_lines([legacy], 4)[0].line_reviewed


@pytest.mark.parametrize("mutation", ["overlap", "infinite", "bad_flag", "duplicate"])
def test_project_rejects_corrupt_boundaries_and_flags(mutation):
    line = LyricLine("One two", 0.5, 1.2, 0.5, "ai", [Word("One", 0.5, 0.8), Word("two", 0.9, 1.2)])
    rows = [asdict(line)]
    if mutation == "overlap":
        rows[0]["words"][1]["start"] = 0.7
    elif mutation == "infinite":
        rows[0]["words"][0]["start"] = float("inf")
    elif mutation == "bad_flag":
        rows[0]["excluded"] = "yes"
    else:
        rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError):
        validate_lines(rows, 4)


def test_project_import_is_undoable_and_read_only(qtbot, mp3, tmp_path, monkeypatch):
    window = timed_window(qtbot, mp3, tmp_path)
    data = json.loads(generate_timing_json(window.track))
    data["metadata"]["title"] = "Imported title"
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data))
    original = window.track.editable_state()
    source_hash = file_hash(mp3)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Apply)
    window.import_project()
    qtbot.waitUntil(
        lambda: window.job is None and window.track.proposed_metadata.title == "Imported title"
    )
    assert window.track.proposed_metadata.title == "Imported title"
    window.undo_stamp()
    assert window.track.editable_state() == original
    assert file_hash(mp3) == source_hash


def test_metadata_proposal_defaults_keep_existing_values(qtbot):
    dialog = MetadataProposalDialog(
        Metadata(title="Song (Edit)", artist="Artist"),
        Metadata(title="Song", artist="Other", album="Album"),
    )
    qtbot.addWidget(dialog)
    assert dialog.selected_fields() == {"album"}
    assert not dialog.cover_check.isChecked()
    dialog.checks["title"].setChecked(True)
    assert dialog.selected_fields() == {"album", "title"}


def test_settings_keep_custom_language_and_validate_before_closing(qtbot, tmp_path):
    settings = Settings(language="it", cache_directory=str(tmp_path / "cache"))
    dialog = SettingsDialog(settings, None)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.settings().language == "it"
    dialog.values["workspace_directory"].setText("")
    dialog.accept()
    assert dialog.isVisible() and "empty" in dialog.validation_message.text()
    assert not dialog.values["ctc_model"].isEnabled()
    dialog.reject()


def test_partial_open_keeps_valid_songs(qtbot, mp3, tmp_path):
    invalid = tmp_path / "broken.mp3"
    invalid.write_text("Not audio")
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    window.open_paths([mp3, invalid])
    qtbot.waitUntil(lambda: window.job is None and bool(window.sessions))
    assert len(window.sessions) == 1 and window.track.path == mp3
    assert "1 failed" in window.job_heading.text()
    window.show_activity()
    assert window.retry_job_button.isVisible()


def test_close_during_job_cancels_then_closes_once(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)

    def wait(context):
        context.cancelled.wait(2)
        context.check()

    window.start_job(wait, lambda result: None, name="Test waiting")
    window.close()
    qtbot.waitUntil(lambda: window.job is None and not window.isVisible())


def test_small_layouts_keep_confirmation_actions_reachable(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.resize(1280, 720)
    assert window.height() == 720 and window.save_button.isVisible()
    window.finish_job_display("Completed", "A synthetic analysis result")
    window.resize(1280, 720)
    assert window.height() == 720
    dialog = WordTimingDialog(window.track.aligned_lines[0], window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.exact_check.setChecked(True)
    dialog.resize(1024, 650)
    dialog.show()
    assert dialog.height() == 650
    assert dialog.apply_button.isVisible()
    dialog.reject()
    save = SaveReviewDialog(window.track, ["Test edit"], window)
    qtbot.addWidget(save)
    save.resize(760, 400)
    save.show()
    assert save.height() == 400
    save.reject()


def test_waveform_is_local_cached_and_cancellable(qtbot, mp3, tmp_path):
    track = read_track(mp3)
    source_hash = file_hash(mp3)
    cache = Cache(tmp_path / "wave-cache")
    peaks = decode_overview(mp3, track.audio.duration, JobContext(), cache, track.content_hash)
    assert 1 <= len(peaks) <= 4096
    assert all(-1 <= low <= high <= 1 for low, high in peaks)
    assert (
        decode_overview(mp3, track.audio.duration, JobContext(), cache, track.content_hash) == peaks
    )
    assert file_hash(mp3) == source_hash
    window = window_for(qtbot, mp3, tmp_path)
    window.wave_action.setChecked(True)
    qtbot.waitUntil(lambda: bool(window.waveform.peaks) and not window.wave_jobs)
    window.waveform.zoom(2)
    assert window.waveform.view_end - window.waveform.view_start < track.audio.duration


def test_pending_text_survives_an_unrelated_undo(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("Pending new lyrics")
    before = window.edit_snapshot()
    window.track.proposed_metadata.album = "Changed album"
    window.record_change("change album", before)
    window.undo_stamp()
    assert window.track.display_lyrics == "Pending new lyrics"
    assert window.lyrics_editor.toPlainText() == "Pending new lyrics"
    assert window._lyrics_pending
    assert window.track.proposed_metadata.album == before["state"]["metadata"]["album"]


def test_import_rechecks_source_file_identity(mp3, tmp_path):
    track = read_track(mp3)
    path = tmp_path / "project.json"
    path.write_text(generate_timing_json(track))
    mp3.write_bytes(mp3.read_bytes() + b"changed outside app")
    with pytest.raises(ValueError, match="changed outside"):
        read_project(path, track, JobContext())


def test_disposable_cache_cleanup_preserves_user_work(tmp_path):
    cache = Cache(tmp_path)
    for category in ("alignment", "waveform", "saved_timings", "models"):
        cache.put(category, {"test": 1}, {"data": "keep or dispose"})
    inventory = disposable_inventory(tmp_path, JobContext())
    assert set(inventory) == {"alignment", "waveform"}
    assert clear_disposable(tmp_path, inventory, ["alignment"], JobContext()) == 1
    assert cache.get("alignment", {"test": 1}) is None
    for category in ("waveform", "saved_timings", "models"):
        assert cache.get(category, {"test": 1}) is not None
    with pytest.raises(ValueError, match="protected"):
        clear_disposable(tmp_path, inventory, ["saved_timings"], JobContext())


def test_lrc_repeats_offset_and_imported_review_status():
    text, lines = parse_lrc(
        "[ar:Artist]\n[offset:+100]\n[00:00.50][00:02.00]Chorus\n[Verse]\nUntimed", 4
    )
    assert text == "Chorus\nChorus\n[Verse]\nUntimed"
    assert [line.start for line in lines] == [0.6, 2.1, None]
    assert len({line.line_id for line in lines}) == 3
    assert all(needs_review(line) for line in lines)
    assert lines[0].source == "imported" and not lines[0].line_reviewed
    with pytest.raises(ValueError, match="outside"):
        parse_lrc("[00:05.00]Too late", 4)


def test_lrc_import_is_read_only_and_undoable(qtbot, mp3, tmp_path, monkeypatch):
    window = timed_window(qtbot, mp3, tmp_path)
    original = window.edit_snapshot()
    original_hash = file_hash(mp3)
    path = tmp_path / "lyrics.lrc"
    path.write_text("[00:00.50]Imported one\n[00:02.00]Imported two")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Apply)
    window.import_lrc()
    qtbot.waitUntil(
        lambda: window.job is None and window.track.aligned_lines[0].text == "Imported one"
    )
    assert all(needs_review(line) for line in window.track.aligned_lines)
    window.undo_stamp()
    assert window.edit_snapshot() == original
    assert file_hash(mp3) == original_hash


def test_saved_exclusions_and_review_survive_reopen(qtbot, mp3, tmp_path, monkeypatch):
    window = timed_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    window.review_decision("words")
    window.table.selectRow(1)
    window.review_decision("excluded")
    original = deepcopy(window.track.aligned_lines)
    monkeypatch.setattr(SaveReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    window.save()
    qtbot.waitUntil(lambda: window.job is None and not window.track.dirty)
    reopened = MainWindow(window.settings)
    qtbot.addWidget(reopened)
    reopened.load_track(read_track(mp3))
    assert reopened.track.aligned_lines == original
    assert reopened.track.aligned_lines[0].words_reviewed
    assert reopened.track.aligned_lines[1].excluded
    assert list((window.workspace_store.directory / "timings").rglob("*.json"))


def test_readiness_check_respects_selected_backend(qtbot, monkeypatch):
    dialog = SettingsDialog(Settings(), None)
    qtbot.addWidget(dialog)
    import importlib.util

    queried = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: queried.append(name))
    dialog.check_readiness()
    assert queried == ["torch", "whisper"]
    assert "Missing: torch, whisper" in dialog.validation_message.text()
    queried.clear()
    dialog.values["ai_backend"].setCurrentIndex(dialog.values["ai_backend"].findData("whisper-ctc"))
    dialog.values["separate_vocals"].setChecked(True)
    dialog.check_readiness()
    assert queried == ["torch", "whisper", "transformers", "demucs"]


def test_large_font_layout_keeps_actions_and_song_identity_visible(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    app = QApplication.instance()
    try:
        apply_theme(app, "dark", 20, True)
        window.track.proposed_metadata.title = "A long title with repeated release details " * 12
        window._title()
        window.focus_section("timing")
        window.finish_job_display("Completed", "Details " * 30)
        window.resize(1280, 720)
        qtbot.wait(10)
        assert window.height() == 720
        assert window.rect().contains(
            window.save_button.mapTo(window, window.save_button.rect().bottomRight())
        )
        assert window.song_heading.toolTip() == window.track.proposed_metadata.title
        assert window.song_list.item(0).text().startswith("• ")
        assert "Unsaved changes" in window.song_list.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
        dialog = WordTimingDialog(window.track.aligned_lines[0], window.player, 4, window)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.exact_check.setChecked(True)
        dialog.resize(1024, 650)
        qtbot.wait(10)
        assert dialog.height() == 650
        assert dialog.apply_button.height() >= dialog.apply_button.fontMetrics().height() + 12
        dialog.reject()
    finally:
        apply_theme(app)


def test_word_draft_cancel_keeps_editing_when_discard_declined(qtbot, mp3, tmp_path, monkeypatch):
    window = timed_window(qtbot, mp3, tmp_path)
    line = window.track.aligned_lines[0]
    original = deepcopy(line)
    dialog = WordTimingDialog(line, window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.table.item(0, 1).setText("00:00.600")
    assert dialog.user_changed and dialog.modified
    monkeypatch.setattr(dialog, "confirm_discard", lambda: False)
    dialog.reject()
    assert dialog.isVisible() and line == original
    monkeypatch.setattr(dialog, "confirm_discard", lambda: True)
    dialog.reject()
    assert not dialog.isVisible() and line == original


def test_listening_review_does_not_rewrite_word_provenance(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines[0])
    dialog = WordTimingDialog(original, window.player, 4, window)
    qtbot.addWidget(dialog)
    dialog.review_check.setChecked(True)
    dialog.apply_words()
    result = dialog.result_line
    assert result.words_reviewed and result.line_reviewed
    assert result.source == original.source
    assert result.confidence == original.confidence and result.words == original.words


def test_section_loop_restarts_inside_its_review_window(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    dialog = WordTimingDialog(window.track.aligned_lines[0], window.player, 4, window, next_start=2)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.loop_check.setChecked(True)
    dialog.playback_position(2500)
    assert window.player.position() == 500
    dialog.loop_check.setChecked(False)
    dialog.playback_position(2500)
    assert window.player.position() == 2000
    assert window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    dialog.reject()


def test_canceled_waveform_decode_does_not_create_cache(mp3, tmp_path):
    track = read_track(mp3)
    context = JobContext()
    context.cancelled.set()
    cache = Cache(tmp_path / "wave-cache")
    with pytest.raises(Cancelled):
        decode_overview(mp3, track.audio.duration, context, cache, track.content_hash)
    assert not cache.directory.exists()


def test_dialog_slider_keyboard_seeks_without_editing_words(qtbot, mp3, tmp_path):
    window = timed_window(qtbot, mp3, tmp_path)
    line = window.track.aligned_lines[0]
    dialog = WordTimingDialog(line, window.player, 4, window, next_start=2)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog.seek_to(700)
    original = deepcopy(dialog.drafts)
    dialog.timeline.setFocus()
    qtbot.keyClick(dialog.timeline, Qt.Key.Key_Right)
    assert window.player.position() == dialog.timeline.value()
    assert window.player.position() > 700
    assert dialog.drafts == original
    dialog.reject()


def test_settings_expand_home_paths():
    settings = Settings(cache_directory="~/cache", workspace_directory="~/drafts")
    assert settings.cache_directory == str(Path.home() / "cache")
    assert settings.workspace_directory == str(Path.home() / "drafts")
