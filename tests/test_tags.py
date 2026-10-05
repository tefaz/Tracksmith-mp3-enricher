from pathlib import Path

import pytest
from mutagen.id3 import APIC, COMM, ID3, SYLT, TXXX, USLT

from tracksmith.cache import file_hash
from tracksmith.jobs import Cancelled, JobContext
from tracksmith.model import Artwork, LyricLine
from tracksmith.tags import compressed_audio_hash, read_track, save_track


def test_read_is_non_mutating_and_state(mp3):
    before = file_hash(mp3)
    track = read_track(mp3)
    assert file_hash(mp3) == before
    assert track.audio.channels == 2
    assert track.audio.sample_rate == 44100
    assert 3.9 < track.audio.duration < 4.2
    assert not track.dirty
    track.proposed_metadata.title = "Changed"
    assert track.dirty
    assert track.existing_metadata.title == ""


def test_save_roundtrip_preserves_audio_and_other_frames(mp3):
    tags = ID3(mp3)
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-4.20 dB"]))
    tags.add(COMM(encoding=3, lang="deu", desc="other app", text=["keep this"]))
    tags.add(USLT(encoding=3, lang="deu", desc="translation", text="keep translation"))
    tags.add(USLT(encoding=3, lang="eng", desc="", text="active lyrics"))
    tags.add(
        SYLT(
            encoding=3,
            lang="deu",
            desc="MPEG frame timestamps",
            format=1,
            type=1,
            text=[("frame based", 40)],
        )
    )
    tags.add(APIC(encoding=3, mime="image/png", type=4, desc="back", data=b"back-cover"))
    tags.save(mp3)
    compressed = compressed_audio_hash(mp3, JobContext())
    track = read_track(mp3)
    track.proposed_metadata.title = "Sonne"
    track.proposed_metadata.artist = "Rammstein"
    track.proposed_metadata.album = "Mutter"
    track.proposed_metadata.album_artist = "Rammstein"
    track.proposed_metadata.date = "2001"
    track.proposed_metadata.track_number = "3/11"
    track.proposed_metadata.genre = "Metal"
    # Selected plain frame is replaced; other languages/descriptions remain.
    tags = ID3(mp3)
    tags.add(USLT(encoding=3, lang="eng", desc="unrelated", text="keep other"))
    tags.save(mp3)
    track = read_track(mp3)
    track.proposed_metadata.title = "Sonne"
    track.proposed_metadata.artist = "Rammstein"
    track.proposed_metadata.album = "Mutter"
    track.proposed_metadata.album_artist = "Rammstein"
    track.proposed_metadata.date = "2001"
    track.proposed_metadata.track_number = "3/11"
    track.proposed_metadata.genre = "Metal"
    track.display_lyrics = "First line\nSecond line\nMissing line"
    track.aligned_lines = [
        LyricLine("Second line", 2.456),
        LyricLine("First line", 0.123),
        LyricLine("Missing line"),
    ]
    # Avoid changing back cover: attach an explicit front-cover key only when present.
    track.artwork_frame_key = None
    track.artwork = Artwork(b"front-cover", "image/jpeg")
    saved = save_track(track)
    assert compressed_audio_hash(mp3, JobContext()) == compressed
    assert not saved.dirty
    actual = ID3(mp3)
    assert str(actual["TIT2"]) == "Sonne"
    assert str(actual["TRCK"]) == "3/11"
    assert str(actual["TDRC"]) == "2001"
    assert actual["TXXX:REPLAYGAIN_TRACK_GAIN"].text == ["-4.20 dB"]
    assert actual["COMM:other app:deu"].text == ["keep this"]
    assert actual["USLT:unrelated:eng"].text == "keep other"
    assert actual["USLT:translation:deu"].text == "keep translation"
    assert any(f.format == 1 for f in actual.getall("SYLT"))
    sync = next(f for f in actual.getall("SYLT") if f.format == 2)
    assert sync.type == 1
    assert sync.text == [("\nFirst line", 123), ("\nSecond line", 2456)]
    assert actual["APIC:back"].data == b"back-cover"
    assert read_track(mp3).display_lyrics == track.display_lyrics
    assert [line.start for line in read_track(mp3).aligned_lines] == [0.123, 2.456, None]
    saved.proposed_metadata.title = "Again"
    save_track(saved)
    assert not Path(str(mp3) + ".bak").exists()


def test_external_change_rejected(mp3):
    track = read_track(mp3)
    track.proposed_metadata.title = "New"
    with mp3.open("ab") as handle:
        handle.write(b"external change")
    before = mp3.read_bytes()
    with pytest.raises(ValueError, match="changed outside"):
        save_track(track)
    assert mp3.read_bytes() == before


def test_verification_failure_keeps_original(mp3, monkeypatch):
    from tracksmith import tags

    track = read_track(mp3)
    track.proposed_metadata.title = "New"
    before = mp3.read_bytes()
    calls = iter([b"original", b"different"])
    monkeypatch.setattr(tags, "compressed_audio_hash", lambda path, context: next(calls))
    with pytest.raises(ValueError, match="Compressed audio verification failed"):
        save_track(track)
    assert mp3.read_bytes() == before
    assert list(mp3.parent.glob(".*.mp3")) == []
    assert not Path(str(mp3) + ".bak").exists()


def test_cancelled_save_keeps_original(mp3):
    track = read_track(mp3)
    before = mp3.read_bytes()
    context = JobContext()
    context.cancelled.set()
    with pytest.raises(Cancelled):
        save_track(track, context=context)
    assert mp3.read_bytes() == before


def test_plain_edit_does_not_destroy_unrelated_sync(mp3):
    tags = ID3(mp3)
    tags.add(SYLT(encoding=3, lang="eng", desc="existing", format=2, type=1, text=[("line", 1200)]))
    tags.save(mp3, v2_version=3)
    track = read_track(mp3)
    track.display_lyrics = "plain edit"
    save_track(track)
    assert ID3(mp3)["SYLT:existing:eng"].text == [("line", 1200)]


def test_artwork_description_collision_preserves_back_cover(mp3):
    tags = ID3(mp3)
    tags.add(APIC(encoding=3, mime="image/png", type=4, desc="Cover", data=b"back"))
    tags.save(mp3)
    track = read_track(mp3)
    track.artwork = Artwork(b"front", "image/jpeg", "Cover")
    save_track(track)
    tags = ID3(mp3)
    assert tags["APIC:Cover"].data == b"back"
    assert tags["APIC:Cover (1)"].data == b"front"


def test_repeated_default_saves_embed_tags_without_companion_files(mp3):
    original_files = set(mp3.parent.iterdir())
    compressed = compressed_audio_hash(mp3, JobContext())
    track = read_track(mp3)
    track.display_lyrics = "Embedded lyrics"
    track.artwork = Artwork(b"embedded-cover", "image/jpeg")
    track.aligned_lines = [LyricLine("Embedded lyrics", 0.5)]
    for title in ("First save", "Second save", "Third save"):
        track.proposed_metadata.title = title
        track = save_track(track)
        assert set(mp3.parent.iterdir()) == original_files
        assert str(ID3(mp3)["TIT2"]) == title
    assert track.display_lyrics == "Embedded lyrics"
    assert ID3(mp3).getall("APIC")[0].data == b"embedded-cover"
    assert ID3(mp3).getall("SYLT")[0].text == [("\nEmbedded lyrics", 500)]
    assert compressed_audio_hash(mp3, JobContext()) == compressed


def test_replacement_failure_keeps_original_and_cleans_temporary(mp3, monkeypatch):
    from tracksmith import tags

    original_files = set(mp3.parent.iterdir())
    original = mp3.read_bytes()
    track = read_track(mp3)
    track.proposed_metadata.title = "New title"

    def fail(source, destination):
        raise OSError("replacement failed")

    monkeypatch.setattr(tags.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        save_track(track)
    assert mp3.read_bytes() == original
    assert set(mp3.parent.iterdir()) == original_files
