from copy import deepcopy
from io import BytesIO

import pytest
from mutagen.id3 import APIC, ID3, PictureType
from PIL import Image
from PySide6.QtWidgets import QDialog

from tracksmith.batch import CATEGORIES, run_batch
from tracksmith.config import Settings
from tracksmith.cover_batch import BatchCoverProvider, CoverSkipped, original_album
from tracksmith.cover_review import CoverReviewDialog
from tracksmith.jobs import JobContext
from tracksmith.model import Artwork, Candidate, LyricLine, Metadata
from tracksmith.tags import compressed_audio_hash, read_track, save_track
from tracksmith.ui import MainWindow


def cover():
    output = BytesIO()
    Image.new("RGB", (40, 40), "green").save(output, format="JPEG")
    return Artwork(output.getvalue(), "image/jpeg")


def release(group="original", year="2000", **updates):
    result = {
        "id": "release-" + group, "title": "Original Album", "date": year,
        "status": "Official", "artist-credit": [{"name": "Example Artist"}],
        "release-group": {
            "id": group, "title": "Original Album", "primary-type": "Album",
            "secondary-types": [], "first-release-date": year,
        },
        "cover-art-archive": {"front": True},
    }
    result.update(updates)
    return result


@pytest.mark.parametrize("problem", ["compilation", "unofficial", "unknown_date", "wrong_artist", "late_edition", "ambiguous", "wrong_album"])
def test_original_album_rejects_uncertain_releases(problem):
    candidate = release()
    album = ""
    candidates = [candidate]
    if problem == "compilation":
        candidate["release-group"]["secondary-types"] = ["Compilation"]
    elif problem == "unofficial":
        candidate["status"] = "Bootleg"
    elif problem == "unknown_date":
        candidate["release-group"]["first-release-date"] = ""
    elif problem == "wrong_artist":
        candidate["artist-credit"] = [{"name": "Various Artists"}]
    elif problem == "late_edition":
        candidate["date"] = "2020"
    elif problem == "ambiguous":
        candidates.append(release("other"))
    elif problem == "wrong_album":
        album = "Something Else"
    with pytest.raises(CoverSkipped):
        original_album(candidates, "Example Artist", album)


def test_original_album_uses_earliest_group_and_original_edition():
    original = release()
    later = release("later", "2005")
    remaster = release(id="remaster", date="2020")
    assert original_album([later, remaster, original], "Example Artist") == original


class Http:
    def __init__(self):
        self.recording = {"title": "Example Song", "artist-credit": [{"name": "Example Artist"}]}
        self.releases = [release()]
        self.images = [{"front": True, "approved": True, "image": "https://archive.org/cover.jpg"}]
        self.downloads = []

    def json(self, url, context):
        context.check()
        if "/recording/" in url:
            return self.recording
        if "musicbrainz.org" in url:
            return {"releases": self.releases, "release-count": len(self.releases)}
        return {"images": self.images}

    def bytes(self, url, context):
        self.downloads.append(url)
        return cover().data


@pytest.mark.parametrize("problem", [None, "weak", "ambiguous", "wrong_title", "variant", "unapproved", "multiple_fronts", "short_clip", "no_artist"])
def test_lookup_requires_audio_identity_and_approved_front(mp3, monkeypatch, problem):
    track = read_track(mp3)
    track.audio.duration = 40
    http = Http()
    provider = BatchCoverProvider(http, "test-key", fingerprint=True)
    monkeypatch.setattr("tracksmith.cover_batch.shutil.which", lambda name: "/bin/fpcalc")
    matches = [Candidate(Metadata(), 0.99, "fingerprint", recording_id="recording")]
    if problem == "weak":
        matches[0].confidence = 0.94
    elif problem == "ambiguous":
        matches.append(Candidate(Metadata(), 0.95, "fingerprint", recording_id="other"))
    elif problem == "wrong_title":
        http.recording["title"] = "Unrelated Clip"
    elif problem == "variant":
        http.recording["disambiguation"] = "live"
    elif problem == "unapproved":
        http.images[0]["approved"] = False
    elif problem == "multiple_fronts":
        http.images.append(deepcopy(http.images[0]))
    elif problem == "short_clip":
        track.audio.duration = 5
    elif problem == "no_artist":
        track.path = track.path.with_name("random sound.mp3")
    monkeypatch.setattr(provider.identification, "identify", lambda *args: matches)
    if problem:
        with pytest.raises(CoverSkipped):
            provider.lookup(track, JobContext())
        assert not http.downloads
    else:
        artwork, album = provider.lookup(track, JobContext())
        assert artwork.mime == "image/jpeg"
        assert album == "Original Album"
        assert len(http.downloads) == 1


class CoverProvider:
    def __init__(self):
        self.lookups = []

    def lookup(self, track, context):
        self.lookups.append(track.path)
        return cover(), "Original Album"

    def recommend(self, track, context):
        artwork, album = self.lookup(track, context)
        return artwork, Candidate(Metadata(album=album), 0.9, "Text search")


@pytest.mark.parametrize("problem", [None, "wrong_artist", "wrong_title", "ambiguous", "duplicate_editions", "no_results"])
def test_name_lookup_needs_no_key_and_skips_uncertain_matches(mp3, monkeypatch, problem):
    track = read_track(mp3)
    track.audio.duration = 40
    http = Http()
    provider = BatchCoverProvider(http)
    matches = [Candidate(
        Metadata(artist="Example Artist", title="Example Song"), 0.95,
        "MusicBrainz text search", recording_id="recording", release_id="release-original",
    )]
    if problem == "wrong_artist":
        matches[0].metadata.artist = "Other Artist"
    elif problem == "wrong_title":
        matches[0].metadata.title = "Other Song"
    elif problem in {"ambiguous", "duplicate_editions"}:
        other = deepcopy(matches[0])
        other.release_id = "other-edition"
        if problem == "ambiguous":
            other.recording_id = "other-recording"
        matches.append(other)
    elif problem == "no_results":
        matches = []
    calls = []

    def search(self, artist, title, context, **kwargs):
        calls.append((artist, title))
        return matches

    def no_fingerprint(*args):
        pytest.fail("Name-based lookup must not use AcoustID or fpcalc")

    monkeypatch.setattr("tracksmith.cover_batch.MusicBrainzProvider.search", search)
    monkeypatch.setattr(provider.identification, "identify", no_fingerprint)
    monkeypatch.setattr("tracksmith.cover_batch.shutil.which", no_fingerprint)
    if problem not in {None, "duplicate_editions"}:
        with pytest.raises(CoverSkipped):
            provider.lookup(track, JobContext())
        assert not http.downloads
    else:
        artwork, album = provider.lookup(track, JobContext())
        assert artwork.mime == "image/jpeg"
        assert album == "Original Album"
    assert calls == [("Example Artist", "Example Song")]


def test_batch_adds_only_artwork_preserving_tags_audio_and_lyrics(mp3):
    track = read_track(mp3)
    track.proposed_metadata = Metadata(title="Example Song", artist="Example Artist", album="Original Album")
    track.display_lyrics = "Keep my lyrics"
    track.aligned_lines = [LyricLine("Keep my lyrics", 1)]
    track = save_track(track)
    before_audio = compressed_audio_hash(mp3, JobContext())
    before_tags = deepcopy(ID3(mp3))
    provider = CoverProvider()
    result = run_batch([track], "covers", Settings(), provider, JobContext())
    assert len(result.saved) == 1
    after = ID3(mp3)
    assert set(after) - set(before_tags) == {"APIC:Cover"}
    assert all(after[key] == value for key, value in before_tags.items())
    assert compressed_audio_hash(mp3, JobContext()) == before_audio
    assert read_track(mp3).artwork.data == cover().data
    assert not run_batch(result.saved, "covers", Settings(), provider, JobContext()).saved
    assert provider.lookups == [mp3]


def test_batch_rereads_disk_to_skip_any_existing_picture(mp3):
    stale = read_track(mp3)
    tags = ID3()
    tags.add(APIC(mime="image/jpeg", type=PictureType.COVER_BACK, desc="Back", data=cover().data))
    tags.save(mp3)
    before = mp3.read_bytes()
    provider = CoverProvider()
    result = run_batch([stale], "covers", Settings(), provider, JobContext())
    assert not result.saved and not provider.lookups
    assert "already has embedded artwork" in result.rows[0][1]
    assert mp3.read_bytes() == before


@pytest.mark.parametrize("failure", [CoverSkipped("uncertain identity"), RuntimeError("network failure")])
def test_cover_batch_skip_or_failure_does_not_stop_queue(mp3, tmp_path, failure):
    second = tmp_path / "second.mp3"
    second.write_bytes(mp3.read_bytes())
    originals = [read_track(mp3), read_track(second)]
    before = mp3.read_bytes()

    class MixedProvider(CoverProvider):
        def lookup(self, track, context):
            if track.path == mp3:
                raise failure
            return super().lookup(track, context)

    result = run_batch(originals, "covers", Settings(), MixedProvider(), JobContext())
    assert [track.path for track in result.saved] == [second]
    assert mp3.read_bytes() == before
    assert str(failure) in result.rows[0][1]


def test_cover_batch_cancellation_keeps_completed_saves(mp3, tmp_path):
    second = tmp_path / "second.mp3"
    second.write_bytes(mp3.read_bytes())
    before = second.read_bytes()
    context = JobContext()
    result = run_batch(
        [read_track(mp3), read_track(second)], "covers", Settings(), CoverProvider(), context,
        on_saved=lambda track: context.cancelled.set(),
    )
    assert result.cancelled
    assert [track.path for track in result.saved] == [mp3]
    assert read_track(mp3).artwork
    assert second.read_bytes() == before


def test_cover_batch_category_includes_search_hidden_songs(qtbot, mp3, tmp_path, monkeypatch):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    tracks = []
    for name in ("first", "hidden", "other-category"):
        path = tmp_path / (name + ".mp3")
        path.write_bytes(mp3.read_bytes())
        track = read_track(path)
        if name == "other-category":
            track.display_lyrics = "Saved lyrics"
            track.aligned_lines = [LyricLine("Saved lyrics")]
            track = save_track(track)
        tracks.append(track)
        window.load_track(track)
    assert not window.batch_covers_action.isEnabled()
    window.category_filter.setCurrentIndex(1)
    assert window.category_filter.currentData() == CATEGORIES[0]
    window.song_search.setText("first")
    assert window.song_list.item(1).isHidden()
    provider = CoverProvider()
    monkeypatch.setattr("tracksmith.ui.BatchCoverProvider", lambda *args: provider)
    monkeypatch.setattr("tracksmith.ui.shutil.which", lambda *args: "/bin/fpcalc")
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    class AcceptingReview(CoverReviewDialog):
        def exec(self):
            assert all(read_track(track.path).artwork is None for track in tracks)
            for index in range(len(self.proposals)):
                self.decide(index, True)
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("tracksmith.ui.CoverReviewDialog", AcceptingReview)
    window.start_batch("covers")
    assert not window.batch_covers_action.isEnabled()
    qtbot.waitUntil(lambda: window.job is None, timeout=10000)
    assert provider.lookups == [tracks[0].path, tracks[1].path]
    assert all(read_track(track.path).artwork for track in tracks[:2])
    assert read_track(tracks[2].path).artwork is None
    assert window.batch_covers_action.isEnabled()
    window.close()
