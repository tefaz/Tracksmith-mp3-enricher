from io import BytesIO

import pytest
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

from song_metadata_enricher.artwork_dialog import ArtworkDialog
from song_metadata_enricher.cache import file_hash
from song_metadata_enricher.config import Settings
from song_metadata_enricher.dialogs import MetadataProposalDialog
from song_metadata_enricher.model import Artwork, Candidate, Metadata
from song_metadata_enricher.tags import read_track
from song_metadata_enricher.ui import MainWindow


@pytest.fixture(autouse=True)
def accept_reviewed_metadata(monkeypatch):
    def approve(dialog):
        dialog.cover_check.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(MetadataProposalDialog, "exec", approve)


def art(color):
    buffer = BytesIO()
    Image.new("RGB", (100, 100), color).save(buffer, format="PNG")
    return Artwork(buffer.getvalue(), "image/png")


class Provider:
    def fetch(self, release_id, context):
        context.check()
        if release_id == "missing":
            raise ValueError("No front cover available")
        return art(release_id)


def candidates(*ids):
    return [
        Candidate(Metadata(artist="Artist", album=name), 0.9, "test", release_id=name)
        for name in ids
    ]


def test_cover_previews_and_release_changes(qtbot):
    dialog = ArtworkDialog(candidates("red", "blue"), Provider())
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.artwork is not None)
    assert not dialog.image.pixmap().isNull()
    assert not dialog.list.item(0).icon().isNull()
    assert dialog.ok_button.isEnabled()
    red = dialog.artwork
    dialog.list.setCurrentRow(1)
    qtbot.waitUntil(lambda: dialog.artwork is not None)
    assert dialog.artwork != red
    assert dialog.image.pixmap().toImage().pixelColor(10, 10).name() == "#0000ff"
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_google_images_search_uses_selected_album_and_album_artist(qtbot, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from PySide6.QtGui import QDesktopServices

    choices = candidates("red", "blue")
    choices[1].metadata.album_artist = "Album Artist"
    dialog = ArtworkDialog(choices, Provider())
    qtbot.addWidget(dialog)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dialog.list.setCurrentRow(1)
    dialog.google_button.click()
    query = parse_qs(urlparse(opened[0]).query)
    assert query["q"] == ['"Album Artist" "blue" album cover']
    assert query["tbm"] == ["isch"]
    dialog.reject()
    qtbot.waitUntil(lambda: not dialog.jobs)


def test_missing_cover_shows_error_and_allows_metadata_only(qtbot):
    dialog = ArtworkDialog(candidates("missing"), Provider(), metadata_selection=True)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: 0 in dialog.failures)
    assert dialog.image.text() == "No cover preview available"
    assert "No front cover available" in dialog.status.text()
    assert dialog.ok_button.isEnabled()
    assert dialog.artwork is None
    dialog.reject()


def test_selected_artwork_appears_in_main_window_without_saving(qtbot, mp3, tmp_path, monkeypatch):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.show()
    window.load_track(read_track(mp3))
    assert window.cover_button.isEnabled()  # No identification/release prerequisite.
    original_hash = file_hash(mp3)
    monkeypatch.setattr(ui, "CoverArtProvider", lambda http: Provider())
    original_exec = ArtworkDialog.exec

    def accept_loaded(dialog):
        timer = QTimer(dialog)
        timer.timeout.connect(lambda: dialog.accept() if dialog.artwork else None)
        timer.start(10)
        QTimer.singleShot(5000, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(ArtworkDialog, "exec", accept_loaded)
    window.choose_artwork(candidates("red"))
    assert window.track.artwork is not None
    assert not window.artwork_label.pixmap().isNull()
    assert window.track.dirty
    assert file_hash(mp3) == original_hash
    window.confirm_discard = lambda: True
    window.close()


def test_metadata_release_selection_also_applies_cover(qtbot, mp3, tmp_path, monkeypatch):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    monkeypatch.setattr(ui, "CoverArtProvider", lambda http: Provider())
    monkeypatch.setattr(
        ui.MusicBrainzProvider, "release", lambda self, candidate, context: candidate
    )
    original_exec = ArtworkDialog.exec

    def accept_loaded(dialog):
        timer = QTimer(dialog)
        timer.timeout.connect(lambda: dialog.accept() if dialog.artwork else None)
        timer.start(10)
        QTimer.singleShot(5000, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(ArtworkDialog, "exec", accept_loaded)
    window.choose_release(candidates("blue"))
    qtbot.waitUntil(lambda: window.job is None)
    assert window.track.proposed_metadata.album == "blue"
    assert window.track.artwork is not None
    assert not window.artwork_label.pixmap().isNull()
    window.confirm_discard = lambda: True
    window.close()


def test_invalid_image_has_visible_error(qtbot):
    class InvalidProvider:
        def fetch(self, release_id, context):
            return Artwork(b"not an image", "image/jpeg")

    dialog = ArtworkDialog(candidates("invalid"), InvalidProvider())
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: 0 in dialog.failures)
    assert "could not be displayed" in dialog.status.text()
    assert not dialog.ok_button.isEnabled()
    dialog.reject()


def test_slow_previous_preview_does_not_replace_new_selection(qtbot):
    import threading

    gate = threading.Event()

    class SlowProvider(Provider):
        def fetch(self, release_id, context):
            if release_id == "red":
                assert gate.wait(3)
            return super().fetch(release_id, context)

    dialog = ArtworkDialog(candidates("red", "blue"), SlowProvider())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.list.setCurrentRow(1)
    qtbot.waitUntil(lambda: dialog.artwork is not None)
    gate.set()
    qtbot.waitUntil(lambda: 0 in dialog.artworks)
    assert dialog.image.pixmap().toImage().pixelColor(10, 10).name() == "#0000ff"
    dialog.reject()


def test_visible_covers_load_before_selecting_each_release(qtbot):
    dialog = ArtworkDialog(
        candidates("missing", "red", "blue"), Provider(), metadata_selection=True
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: len(dialog.artworks) == 2 and len(dialog.failures) == 1)
    assert dialog.list.currentRow() == 0
    assert "Cover unavailable" in dialog.list.item(0).text()
    assert "Cover ready" in dialog.list.item(1).text()
    assert "Cover ready" in dialog.list.item(2).text()
    assert "2 covers ready" in dialog.preview_count.text()
    dialog.hide_missing.setChecked(True)
    assert dialog.list.item(0).isHidden()
    assert dialog.list.currentRow() == 1
    assert dialog.artwork is not None
    dialog.filter_edit.setText("blue")
    assert dialog.list.currentRow() == 2
    dialog.filter_edit.setText("nothing matches")
    assert dialog.list.currentRow() == -1
    assert not dialog.ok_button.isEnabled()
    dialog.reject()


def test_same_release_downloaded_once_for_multiple_recordings(qtbot):
    calls = []

    class CountingProvider(Provider):
        def fetch(self, release_id, context):
            calls.append(release_id)
            return super().fetch(release_id, context)

    choices = candidates("red", "red")
    choices[1].recording_id = "other recording"
    dialog = ArtworkDialog(choices, CountingProvider())
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: len(dialog.artworks) == 2)
    assert calls == ["red"]
    dialog.reject()


def test_closing_chooser_stops_prefetch_and_cancels_running_requests(qtbot):
    import threading

    gate = threading.Event()
    calls = []

    class WaitingProvider(Provider):
        def fetch(self, release_id, context):
            calls.append(release_id)
            gate.wait(3)
            context.check()
            return super().fetch(release_id, context)

    dialog = ArtworkDialog(candidates("red", "blue", "green", "yellow"), WaitingProvider())
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: len(calls) == 2)
    dialog.reject()
    assert all(job.context.cancelled.is_set() for job in dialog.jobs.values())
    gate.set()
    qtbot.waitUntil(lambda: not dialog.jobs)
    assert len(calls) == 2


def test_metadata_search_uses_one_chooser_with_all_recordings_and_covers(
    qtbot, mp3, tmp_path, monkeypatch
):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    original_hash = file_hash(mp3)
    choices = candidates("missing", "blue")
    choices[0].recording_id = "recording without a cover"
    choices[1].recording_id = "recording with another edition"
    monkeypatch.setattr(ui, "CoverArtProvider", lambda http: Provider())
    monkeypatch.setattr(ui.MusicBrainzProvider, "search_covers", lambda *args: choices)
    monkeypatch.setattr(ui.MusicBrainzProvider, "release", lambda self, candidate, ctx: candidate)
    monkeypatch.setattr(
        ui.CandidateDialog,
        "exec",
        lambda self: (_ for _ in ()).throw(
            AssertionError("A text-only first chooser would hide the cover alternatives")
        ),
    )
    original_exec = ArtworkDialog.exec
    opened = []

    def accept_alternative(dialog):
        opened.append(dialog)
        assert dialog.metadata_selection
        timer = QTimer(dialog)

        def ready():
            if 0 in dialog.failures and 1 in dialog.artworks:
                dialog.list.setCurrentRow(1)
                dialog.accept()

        timer.timeout.connect(ready)
        timer.start(10)
        QTimer.singleShot(5000, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(ArtworkDialog, "exec", accept_alternative)
    window.find_metadata()
    qtbot.waitUntil(lambda: window.job is None and window.track.artwork is not None)
    assert len(opened) == 1
    assert window.track.proposed_metadata.album == "blue"
    assert len(window.release_candidates) == 2
    assert file_hash(mp3) == original_hash
    window.confirm_discard = lambda: True
    window.close()


def test_find_cover_replaces_stale_suggestions_with_fresh_matches(
    qtbot, mp3, tmp_path, monkeypatch
):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    window.release_candidates = candidates("missing")
    requested = []
    results = []

    def search(provider, artist, title, context):
        requested.append((artist, title))
        return candidates("blue")

    monkeypatch.setattr(ui.MusicBrainzProvider, "search_covers", search)
    monkeypatch.setattr(window, "choose_artwork", lambda choices: results.extend(choices))
    window.find_artwork()
    qtbot.waitUntil(lambda: bool(results))
    assert {candidate.release_id for candidate in results} == {"blue"}
    assert requested == [("Example Artist", "Example Song")]
    assert window.track.proposed_metadata.album == ""
    window.close()


def test_cover_search_uses_remembered_choices_when_offline(qtbot, mp3, tmp_path, monkeypatch):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    window.release_candidates = candidates("red", "blue")
    results = []

    def offline(*args):
        raise RuntimeError("Provider could not be reached")

    monkeypatch.setattr(ui.MusicBrainzProvider, "search_covers", offline)
    monkeypatch.setattr(window, "choose_artwork", lambda choices: results.extend(choices))
    window.find_artwork()
    qtbot.waitUntil(lambda: len(results) == 2)
    window.close()


def test_metadata_only_choice_preserves_existing_cover(qtbot, mp3, tmp_path, monkeypatch):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    existing = art("red")
    window.apply_artwork(existing)
    monkeypatch.setattr(ui, "CoverArtProvider", lambda http: Provider())
    monkeypatch.setattr(ui.MusicBrainzProvider, "release", lambda self, candidate, ctx: candidate)
    original_exec = ArtworkDialog.exec

    def accept_details_only(dialog):
        timer = QTimer(dialog)
        timer.timeout.connect(lambda: dialog.accept() if dialog.failures else None)
        timer.start(10)
        QTimer.singleShot(5000, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(ArtworkDialog, "exec", accept_details_only)
    window.choose_release(candidates("missing"))
    qtbot.waitUntil(lambda: window.job is None)
    assert window.track.proposed_metadata.album == "missing"
    assert window.track.artwork == existing
    window.confirm_discard = lambda: True
    window.close()


def test_accepted_cover_is_kept_if_extra_release_details_fail(qtbot, mp3, tmp_path, monkeypatch):
    import song_metadata_enricher.ui as ui

    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.load_track(read_track(mp3))
    monkeypatch.setattr(ui, "CoverArtProvider", lambda http: Provider())
    errors = []
    monkeypatch.setattr(window, "error", errors.append)

    def offline(*args):
        raise RuntimeError("Release details unavailable")

    monkeypatch.setattr(ui.MusicBrainzProvider, "release", offline)
    original_exec = ArtworkDialog.exec

    def accept_loaded(dialog):
        timer = QTimer(dialog)
        timer.timeout.connect(lambda: dialog.accept() if dialog.artwork else None)
        timer.start(10)
        QTimer.singleShot(5000, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(ArtworkDialog, "exec", accept_loaded)
    window.choose_release(candidates("blue"))
    assert window.track.artwork is not None
    assert window.track.proposed_metadata.album == "blue"
    qtbot.waitUntil(lambda: window.job is None)
    assert "Release details unavailable" in window.job_detail.text()
    assert window.track.artwork is not None
    window.confirm_discard = lambda: True
    window.close()
