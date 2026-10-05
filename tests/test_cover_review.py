from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from test_cover_batch import CoverProvider, cover

from tracksmith.batch import run_batch
from tracksmith.config import Settings
from tracksmith.cover_batch import (
    AcceptedCoverProvider,
    BatchCoverProvider,
    CoverProposal,
    CoverSearchResult,
    find_cover_proposals,
)
from tracksmith.cover_review import CoverReviewDialog
from tracksmith.jobs import JobContext
from tracksmith.model import Candidate, Metadata
from tracksmith.tags import read_track
from tracksmith.theme import apply_theme
from tracksmith.ui import MainWindow


def proposals(mp3, tmp_path):
    result = CoverSearchResult()
    for name in ("First Song", "Second Song", "Unreviewed Song"):
        path = tmp_path / f"Artist - {name}.mp3"
        path.write_bytes(mp3.read_bytes())
        result.proposals.append(CoverProposal(
            read_track(path), cover(), Candidate(
                Metadata(artist="Artist", title=name, album="Suggested Album"), 0.8,
                "MusicBrainz text search",
            ),
        ))
    return result


def test_review_decisions_are_explicit_changeable_and_only_accepted_images_save(qtbot, mp3, tmp_path):
    result = proposals(mp3, tmp_path)
    before = {p.track.path: p.track.path.read_bytes() for p in result.proposals}
    dialog = CoverReviewDialog(result)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.save_button.isEnabled()
    assert not dialog.accepted_proposals
    qtbot.mouseClick(dialog.cards[0][0], Qt.MouseButton.LeftButton)
    assert dialog.decisions == [True, None, None]
    assert dialog.cards[0][0].property("tone") == "neutral"
    assert dialog.cards[0][1].property("tone") == "accepted"
    qtbot.mouseClick(dialog.cards[0][0], Qt.MouseButton.LeftButton)
    assert not dialog.save_button.isEnabled()
    assert dialog.cards[0][0].property("tone") == "positive"
    qtbot.mouseClick(dialog.cards[0][0], Qt.MouseButton.LeftButton)
    assert dialog.accepted_proposals == [result.proposals[0]]
    assert all(path.read_bytes() == data for path, data in before.items())
    accepted = dialog.accepted_proposals
    saved = run_batch(
        [p.track for p in accepted], "covers", Settings(), AcceptedCoverProvider(accepted), JobContext()
    )
    assert len(saved.saved) == 1
    assert read_track(accepted[0].track.path).artwork.data == accepted[0].artwork.data
    assert all(p.track.path.read_bytes() == before[p.track.path] for p in result.proposals[1:])


def test_search_is_read_only_and_cancel_retains_completed_previews(mp3, tmp_path):
    result = proposals(mp3, tmp_path)
    tracks = [p.track for p in result.proposals]
    before = {t.path: t.path.read_bytes() for t in tracks}
    context = JobContext()

    class CancellingProvider(CoverProvider):
        def recommend(self, track, child):
            if self.lookups:
                child.cancelled.set()
                child.check()
            return super().recommend(track, child)

    found = find_cover_proposals(tracks, CancellingProvider(), context)
    assert found.cancelled
    assert len(found.proposals) == 1
    assert all(path.read_bytes() == data for path, data in before.items())


def test_accept_all_selects_without_saving_or_closing_and_can_be_undone(qtbot, mp3, tmp_path):
    result = proposals(mp3, tmp_path)
    before = {p.track.path: p.track.path.read_bytes() for p in result.proposals}
    dialog = CoverReviewDialog(result)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.accept_all_button, Qt.MouseButton.LeftButton)
    assert dialog.decisions == [True] * len(result.proposals)
    assert dialog.accepted_proposals == result.proposals
    assert dialog.save_button.isEnabled()
    assert not dialog.accept_all_button.isEnabled()
    assert dialog.isVisible()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert all(path.read_bytes() == data for path, data in before.items())
    assert all(button.property("tone") == "neutral" for button, status in dialog.cards)
    assert all(status.property("tone") == "accepted" for button, status in dialog.cards)
    qtbot.mouseClick(dialog.cards[1][0], Qt.MouseButton.LeftButton)
    assert dialog.decisions == [True, False, True]
    assert dialog.accept_all_button.isEnabled()
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize("font_size", [10, 20])
@pytest.mark.parametrize("width", [720, 1040])
def test_long_card_text_stays_below_cover_and_inside_card(qtbot, mp3, tmp_path, font_size, width):
    app = QApplication.instance()
    result = proposals(mp3, tmp_path)
    result.proposals[0].track.existing_metadata.title = "A long song title with many words " * 8
    result.proposals[0].candidate.metadata.album = "An album title that also needs wrapping " * 4
    try:
        apply_theme(app, "dark", font_size)
        dialog = CoverReviewDialog(result)
        qtbot.addWidget(dialog)
        dialog.resize(width, 760)
        dialog.show()
        qtbot.wait(10)
        for button, status in dialog.cards:
            card = button.parentWidget()
            box = card.layout()
            previous_bottom = -1
            for index in range(box.count()):
                widget = box.itemAt(index).widget()
                assert widget.y() > previous_bottom
                assert widget.geometry().bottom() < card.height()
                if widget.hasHeightForWidth():
                    assert widget.height() >= widget.heightForWidth(widget.width())
                previous_bottom = widget.geometry().bottom()
    finally:
        apply_theme(app, "dark", 10)


def test_suggestions_allow_fuzzy_names_and_short_clips(mp3, monkeypatch):
    track = read_track(mp3)
    candidate = Candidate(
        Metadata(artist="Example Artist", title="Example Song!", album="An Album"),
        0.81, "Text search", recording_id="r", release_id="edition",
    )
    monkeypatch.setattr(
        "tracksmith.cover_batch.MusicBrainzProvider.search_covers",
        lambda *args: [candidate],
    )
    monkeypatch.setattr("tracksmith.cover_batch.CoverArtProvider.fetch", lambda *args: cover())
    artwork, found = BatchCoverProvider(None).recommend(track, JobContext())
    assert found == candidate
    assert artwork.data == cover().data
    assert read_track(mp3).artwork is None


@pytest.mark.parametrize("cancel", [False, True])
def test_ui_review_saves_only_accepted_or_nothing_on_cancel(qtbot, mp3, tmp_path, monkeypatch, cancel):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    result = proposals(mp3, tmp_path)
    for p in result.proposals:
        window.load_track(deepcopy(p.track))
    before = {p.track.path: p.track.path.read_bytes() for p in result.proposals}

    class ChoosingReview(CoverReviewDialog):
        def exec(self):
            self.decide(0, True)
            self.decide(1, False)
            return QDialog.DialogCode.Rejected if cancel else QDialog.DialogCode.Accepted

    monkeypatch.setattr("tracksmith.ui.CoverReviewDialog", ChoosingReview)
    window.review_batch_covers(result)
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    for i, p in enumerate(result.proposals):
        if i == 0 and not cancel:
            assert read_track(p.track.path).artwork.data == p.artwork.data
        else:
            assert p.track.path.read_bytes() == before[p.track.path]
    window.close()


def test_file_changed_after_review_is_not_overwritten(mp3, tmp_path):
    result = proposals(mp3, tmp_path)
    approved = result.proposals[0]
    from mutagen.id3 import ID3, TIT2

    tags = ID3(approved.track.path)
    tags.add(TIT2(encoding=3, text=["External edit"]))
    tags.save(approved.track.path)
    before = approved.track.path.read_bytes()
    saved = run_batch(
        [approved.track], "covers", Settings(), AcceptedCoverProvider([approved]), JobContext()
    )
    assert not saved.saved
    assert "file changed" in saved.rows[0][1]
    assert approved.track.path.read_bytes() == before
