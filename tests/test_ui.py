from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QMessageBox

from tracksmith.config import Settings
from tracksmith.dialogs import SaveReviewDialog
from tracksmith.tags import read_track
from tracksmith.ui import MainWindow


def window_for(qtbot, mp3, tmp_path):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    window.load_track(read_track(mp3))
    window.confirm_discard = lambda: True
    return window


def test_manual_workflow(qtbot, mp3, tmp_path, monkeypatch):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("[Verse]\nFirst line\nSecond line")
    window.apply_lyrics()
    assert window.table.rowCount() == 2
    qtbot.waitUntil(
        lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia, timeout=10000
    )
    window.player.setPosition(1234)
    window.stamp_line()
    assert window.track.aligned_lines[0].start == 1.234
    assert window.table.currentRow() == 0
    window.table.selectRow(1)
    window.player.setPosition(2456)
    window.stamp_line()
    window.position_changed(1800)
    assert window._active == 0
    assert window.track.aligned_lines[0].start == 1.234
    window.table.item(1, 0).setText("00:02.600")
    assert window.track.aligned_lines[1].start == 2.6
    path = tmp_path / "export.lrc"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    window.export_lrc()
    assert path.read_text() == "[00:01.23]First line\n[00:02.60]Second line\n"
    assert window.track.dirty
    window.grab().save(str(tmp_path / "manual-workflow.png"))
    window.close()


def test_typing_does_not_trigger_shortcuts(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.activateWindow()
    window.lyrics_editor.setFocus()
    qtbot.keyClicks(window.lyrics_editor, "hello world")
    qtbot.keyClick(window.lyrics_editor, Qt.Key.Key_Return)
    assert window.lyrics_editor.toPlainText() == "hello world\n"
    assert window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    window.close()


def test_background_open(qtbot, mp3, tmp_path):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    window.open_path(mp3)
    assert not window.open_action.isEnabled()
    qtbot.waitUntil(lambda: window.track is not None and window.job is None, timeout=10000)
    assert window.open_action.isEnabled()
    assert not window.track.dirty
    window.close()


def test_start_without_song_and_load_button(qtbot, mp3, tmp_path, monkeypatch):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    assert window.track is None
    assert window.player.source().isEmpty()
    assert window.load_button.isEnabled()
    assert not window.play_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert not window.save_button.isEnabled()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(mp3)], ""))
    qtbot.mouseClick(window.load_button, Qt.MouseButton.LeftButton)
    assert not window.load_button.isEnabled()
    qtbot.waitUntil(lambda: window.track is not None and window.job is None, timeout=10000)
    assert window.track.path == mp3
    assert window.load_button.isEnabled()
    assert window.play_button.isEnabled()
    assert window.stop_button.isEnabled()
    assert not window.track.dirty
    window.close()


def test_keyboard_timestamp_and_editing_focus(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("First line\nSecond line")
    window.apply_lyrics()
    window.track.aligned_lines[0].set_timestamp(1, window.track.audio.duration)
    window.render_table()
    window.activateWindow()
    window.table.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow() and window.table.hasFocus())
    qtbot.keyClick(window.table, Qt.Key.Key_Right)
    assert window.track.aligned_lines[0].start == 1.1
    qtbot.keyClick(window.table, Qt.Key.Key_Left, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert abs(window.track.aligned_lines[0].start - 0.1) < 0.0001
    qtbot.keyClick(window.table, Qt.Key.Key_Down)
    assert window.table.currentRow() == 1
    edit = window.metadata_fields["title"]
    edit.setFocus()
    qtbot.keyClicks(edit, "Song Title")
    qtbot.keyClick(edit, Qt.Key.Key_Left)
    assert window.track.proposed_metadata.title == "Song Title"
    assert window.track.aligned_lines[1].start is None
    window.close()


def test_ui_save_roundtrip(qtbot, mp3, tmp_path, monkeypatch):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("First line")
    window.apply_lyrics()
    window.track.aligned_lines[0].set_timestamp(1.234, window.track.audio.duration)
    window.render_table()
    monkeypatch.setattr(SaveReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    window.save()
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert read_track(mp3).aligned_lines[0].start == 1.234
    assert not window.track.dirty
    assert window.track.status == "Saved"
    assert window.player.source().isLocalFile()
    window.close()


def test_real_player_play_pause_seek_and_highlight(qtbot, mp3, tmp_path):
    from tracksmith.model import LyricLine

    window = window_for(qtbot, mp3, tmp_path)
    window.audio_output.setMuted(True)
    window.track.aligned_lines = [LyricLine("First", 0.2), LyricLine("Second", 0.8)]
    window.render_table()
    qtbot.waitUntil(
        lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia, timeout=10000
    )
    window.player.setPosition(600)
    window.toggle_play()
    qtbot.waitUntil(lambda: window.player.position() > 900, timeout=5000)
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    assert window._active == 1
    window.toggle_play()
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    window.seek_line(0)
    qtbot.waitUntil(lambda: window.player.position() == 200)
    assert window._active == 0
    window.close()


def test_stop_button_and_backspace_reset_playback(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.audio_output.setMuted(True)
    window.activateWindow()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.waitUntil(
        lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia, timeout=10000
    )
    for focus in (window.table, window.play_button, window.timeline):
        window.player.setPosition(1200)
        window.player.play()
        qtbot.waitUntil(lambda: window.player.position() > 1200, timeout=5000)
        focus.setFocus()
        qtbot.keyClick(focus, Qt.Key.Key_Backspace)
        assert window.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        assert window.player.position() == 0
        assert window.timeline.value() == 0
        assert not window.table.playback_locked
        assert window.play_button.text() == "▶  Play (space)"

    window.player.setPosition(1200)
    window.player.play()
    qtbot.waitUntil(lambda: window.player.position() > 1200, timeout=5000)
    qtbot.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert window.player.position() == 0
    window.player.setPosition(1200)
    window.player.play()
    qtbot.waitUntil(lambda: window.player.position() > 1200, timeout=5000)
    window.player.pause()
    qtbot.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert window.player.position() == 0
    window.close()


def test_backspace_in_text_fields_keeps_playing(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.audio_output.setMuted(True)
    window.activateWindow()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    window.player.play()
    for editor in (window.metadata_fields["title"], window.lyrics_editor):
        editor.setFocus()
        qtbot.keyClicks(editor, "abc")
        qtbot.keyClick(editor, Qt.Key.Key_Backspace)
        content = editor.text() if hasattr(editor, "text") else editor.toPlainText()
        assert content.endswith("ab")
        assert window.is_playing()
    window.close()


def test_song_list_keeps_unsaved_lyrics_metadata_timing_and_release(qtbot, mp3, tmp_path):
    from tracksmith.model import Candidate, Metadata

    window = window_for(qtbot, mp3, tmp_path)
    first = window.track
    window.metadata_fields["title"].setText("Edited title")
    window.metadata_edited()
    window.lyrics_editor.setPlainText("First song line")
    window.apply_lyrics()
    first.aligned_lines[0].set_timestamp(1.234, first.audio.duration)
    window.render_table()
    window.release_id = "release-one"
    window.release_candidates = [Candidate(Metadata(album="Album"), 0.9, "test")]
    # Keep unapplied lyric edits too: switching must not rebuild the timing table.
    window.lyrics_editor.setPlainText("First song line\nPending line")
    second_path = tmp_path / "Second song.mp3"
    second_path.write_bytes(mp3.read_bytes())
    window.load_track(read_track(second_path))
    second = window.track
    window.lyrics_editor.setPlainText("Second song line")
    window.apply_lyrics()
    assert window.song_list.count() == 2
    assert window.track is second
    window.song_list.setCurrentRow(0)
    assert window.track is first
    assert window.metadata_fields["title"].text() == "Edited title"
    assert window.lyrics_editor.toPlainText() == "First song line\nPending line"
    assert window._lyrics_pending
    assert first.aligned_lines[0].start == 1.234
    assert len(first.aligned_lines) == 1
    assert window.release_id == "release-one"
    assert window.release_candidates[0].metadata.album == "Album"
    assert window.song_list.item(0).text().startswith("• ")
    assert "Unsaved" in window.song_list.item(0).toolTip()
    window.open_path(second_path)  # Reopening selects existing state, not disk tags.
    assert window.song_list.count() == 2
    assert window.track is second
    assert window.lyrics_editor.toPlainText() == "Second song line"
    window.close()


def test_loading_multiple_songs_and_saving_updates_only_selected_entry(
    qtbot, mp3, tmp_path, monkeypatch
):
    second = tmp_path / "Second.mp3"
    second.write_bytes(mp3.read_bytes())
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", lambda *args: ([str(mp3), str(second)], "")
    )
    window.load_button.click()
    qtbot.waitUntil(lambda: window.job is None and len(window.sessions) == 2)
    assert window.track.path == mp3
    window.lyrics_editor.setPlainText("Keep first song edits")
    window.apply_lyrics()
    first = window.track
    window.song_list.setCurrentRow(1)
    window.lyrics_editor.setPlainText("Save second song edits")
    window.apply_lyrics()
    monkeypatch.setattr(SaveReviewDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    window.save()
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert len(window.sessions) == 2
    assert window.song_list.currentRow() == 1
    assert not window.track.dirty
    assert read_track(second).display_lyrics == "Save second song edits"
    assert not read_track(mp3).display_lyrics
    window.song_list.setCurrentRow(0)
    assert window.track is first
    assert first.dirty
    assert window.lyrics_editor.toPlainText() == "Keep first song edits"
    window.confirm_discard = lambda: True
    window.close()


def test_closing_checks_unsaved_songs_even_when_selected_song_is_clean(
    qtbot, mp3, tmp_path, monkeypatch
):
    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("Unsaved first song")
    second = tmp_path / "Clean.mp3"
    second.write_bytes(mp3.read_bytes())
    window.load_track(read_track(second))
    assert not window.track.dirty
    questions = []

    def ask(dialog):
        questions.extend(check.text() for check in dialog.findChildren(QCheckBox))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", ask)
    del window.confirm_discard  # Restore real method; helper bypasses close prompts.
    assert not window.confirm_discard()
    assert questions == [mp3.name]
    window.confirm_discard = lambda: True
    window.close()


def test_progress_explains_stage_activity_and_possible_stall(qtbot, mp3, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import tracksmith.ui as ui

    window = window_for(qtbot, mp3, tmp_path)
    clock = [100.0]
    monkeypatch.setattr(ui, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def waiting(context):
        while not context.cancelled.wait(0.01):
            pass
        context.check()

    window.start_job(waiting, lambda result: None, analysis=True)
    window.job_progress("Download alignment model: 20 / 360 MB", 65)
    assert not window.activity_dialog.isVisible()
    assert window.cancel_job_action.isEnabled()
    assert window.progress.isVisible()
    assert window.cancel_button.isVisible()
    assert window.footer.isAncestorOf(window.progress)
    assert "20 / 360 MB" in window.next_step.text()
    window.show_activity()
    assert window.job_panel.isVisible()
    assert "Step 5 of 6" in window.job_heading.text()
    assert window.progress.value() == 65
    assert (
        "estimate" in window.progress.format().lower()
        or "estimated" in window.progress.format().lower()
    )
    assert "20 / 360 MB" in window.job_detail.text()
    assert not window.song_list.isEnabled()
    clock[0] = 145
    window.job_activity({"cpu_active": False})
    assert "45s ago" in window.job_activity_label.text()
    assert "waiting or stalled" in window.job_activity_label.text()
    window.job_activity({"cpu_active": True})
    assert "using the CPU" in window.job_activity_label.text()
    window.cancel_job()
    qtbot.waitUntil(lambda: window.job is None)
    assert "cancelled" in window.job_heading.text().lower()
    assert window.song_list.isEnabled()
    window.close()


def test_analysis_summary_remains_visible_and_export_is_explained(qtbot, mp3, tmp_path):
    from tracksmith.model import LyricLine

    window = window_for(qtbot, mp3, tmp_path)
    window.apply_alignment([LyricLine("Missing"), LyricLine("Timed", 1, 2, 0.95, "ai")])
    assert not window.activity_dialog.isVisible()
    window.show_activity()
    assert window.job_panel.isVisible()
    assert "Analysis finished" in window.job_heading.text()
    assert "1 of 2 lines timed" in window.job_detail.text()
    assert "separate file (.lrc)" in window.export_action.text()
    assert "Optional" in window.export_action.statusTip()
    assert not hasattr(window, "export_button")
    window.close()


def test_cached_analysis_is_labelled_and_fresh_action_bypasses_it(
    qtbot, mp3, tmp_path, monkeypatch
):
    import tracksmith.ui as ui
    from tracksmith.model import LyricLine

    window = window_for(qtbot, mp3, tmp_path)
    window.lyrics_editor.setPlainText("Known words")
    window.apply_lyrics()
    calls = []

    def analyze(track, settings, context, fresh=False):
        calls.append(fresh)
        if not fresh:
            context.progress("Load cached alignment — unchanged audio and lyrics", 100)
        return [LyricLine("Known words", 0.5, 1, 0.9, "ai")]

    monkeypatch.setattr(ui, "run_alignment", analyze)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.analyze()
    qtbot.waitUntil(lambda: window.job is None)
    assert "Previous analysis loaded" in window.job_heading.text()
    assert "fresh analysis" in window.job_detail.text()
    window.fresh_analysis_action.trigger()
    qtbot.waitUntil(lambda: window.job is None)
    assert calls == [False, True]
    assert "Analysis finished" in window.job_heading.text()
    window.close()
