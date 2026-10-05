from pathlib import Path

import pytest
from mutagen.id3 import APIC, COMM, ID3, PRIV, SYLT, TXXX, USLT

from tracksmith.cache import file_hash
from tracksmith.embedded_timing import TIMING_OWNER
from tracksmith.jobs import Cancelled, JobContext
from tracksmith.model import Artwork, LyricLine, Word
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


def test_mixed_line_and_word_timings_roundtrip_inside_mp3(mp3, tmp_path):
    track = read_track(mp3)
    track.display_lyrics = "Line only\nHello,  world!\nMissing\nHello,  world!\nNot sung"
    track.aligned_lines = [
        LyricLine("Line only", 0.1),
        LyricLine(
            "Hello,  world!",
            0.7,
            1.6,
            0.8,
            "ai",
            [Word("Hello", 0.7, 0.9, 0.9), Word("world", 1.2, 1.6, 0.8)],
            line_reviewed=True,
            words_reviewed=True,
        ),
        LyricLine("Missing"),
        LyricLine(
            "Hello,  world!",
            2,
            2.9,
            1,
            "manual",
            [Word("Hello", 2, 2.3, 1, "manual"), Word("world", 2.5, 2.9, 1, "manual")],
        ),
        LyricLine("Not sung", excluded=True),
    ]
    before_audio = compressed_audio_hash(mp3, JobContext())
    before_files = set(mp3.parent.iterdir())
    save_track(track)
    tags = ID3(mp3)
    assert tags.getall("SYLT")[0].text == [
        ("\nLine only", 100),
        ("\nHello,", 700),
        ("  world!", 1200),
        ("\nHello,", 2000),
        ("  world!", 2500),
    ]
    assert any(frame.owner == TIMING_OWNER for frame in tags.getall("PRIV"))
    assert compressed_audio_hash(mp3, JobContext()) == before_audio
    assert set(mp3.parent.iterdir()) == before_files
    # A copied MP3 needs no application cache or companion file to restore its words.
    copied = tmp_path / "portable.mp3"
    copied.write_bytes(mp3.read_bytes())
    reopened = read_track(copied)
    assert reopened.aligned_lines == track.aligned_lines
    assert [line.line_id for line in reopened.aligned_lines] == [
        line.line_id for line in track.aligned_lines
    ]
    assert not reopened.dirty


@pytest.mark.parametrize("private_data", ["missing", "malformed", "stale"])
def test_standard_word_cues_work_without_valid_private_data(mp3, private_data):
    tags = ID3(mp3)
    tags.add(
        SYLT(
            encoding=3,
            lang="eng",
            desc="Tracksmith",
            format=2,
            type=1,
            text=[("\nHello", 500), (" world", 1000), ("\nLine only", 2000)],
        )
    )
    if private_data != "missing":
        tags.add(
            PRIV(
                owner=TIMING_OWNER,
                data=(
                    b"not json"
                    if private_data == "malformed"
                    else b'{"version":1,"duration":4,"lyrics":"wrong","lines":[]}'
                ),
            )
        )
    tags.save(mp3)
    track = read_track(mp3)
    assert [line.text for line in track.aligned_lines] == ["Hello world", "Line only"]
    assert [line.start for line in track.aligned_lines] == [0.5, 2]
    assert [word.start for word in track.aligned_lines[0].words] == [0.5, 1]
    assert all(word.source == "estimated" for word in track.aligned_lines[0].words)
    assert not track.aligned_lines[1].words


def test_clear_words_then_clear_lines_removes_embedded_data_and_keeps_other_frames(mp3):
    tags = ID3(mp3)
    tags.add(PRIV(owner="other.player", data=b"keep me"))
    tags.save(mp3)
    track = read_track(mp3)
    track.display_lyrics = "Hello world"
    track.aligned_lines = [
        LyricLine(
            "Hello world",
            0.5,
            1.5,
            words=[Word("Hello", 0.5, 0.8), Word("world", 1, 1.5)],
        )
    ]
    track = save_track(track)
    track.aligned_lines[0].words = []
    track.aligned_lines[0].end = None
    track = save_track(track)
    tags = ID3(mp3)
    assert tags.getall("SYLT")[0].text == [("\nHello world", 500)]
    assert [(frame.owner, frame.data) for frame in tags.getall("PRIV")] == [
        ("other.player", b"keep me")
    ]
    track.aligned_lines[0].set_timestamp(None, track.audio.duration)
    save_track(track)
    assert not ID3(mp3).getall("SYLT")
    assert read_track(mp3).display_lyrics == "Hello world"


def test_metadata_save_migrates_cached_words_into_mp3(mp3):
    track = read_track(mp3)
    track.display_lyrics = "Hello world"
    track.aligned_lines = [LyricLine("Hello world", 0.5)]
    save_track(track)
    track = read_track(mp3)
    track.aligned_lines[0].words = [Word("Hello", 0.5, 0.8), Word("world", 1, 1.5)]
    track.aligned_lines[0].end = 1.5
    # Existing installations restore cached words as the saved baseline.
    track.mark_saved()
    track.proposed_metadata.title = "Changed title"
    save_track(track)
    assert read_track(mp3).aligned_lines[0].words == track.aligned_lines[0].words


def test_invalid_word_timing_save_keeps_original(mp3):
    track = read_track(mp3)
    track.display_lyrics = "Hello world"
    track.aligned_lines = [
        LyricLine(
            "Hello world",
            0.5,
            1.5,
            words=[Word("Hello", 0.5, 1.2), Word("world", 1, 1.5)],
        )
    ]
    original = mp3.read_bytes()
    with pytest.raises(ValueError, match="overlap"):
        save_track(track)
    assert mp3.read_bytes() == original


def test_external_word_edit_does_not_restore_stale_private_timing(mp3):
    track = read_track(mp3)
    track.display_lyrics = "Hello world"
    track.aligned_lines = [
        LyricLine(
            "Hello world",
            0.5,
            1.5,
            words=[Word("Hello", 0.5, 0.8), Word("world", 1, 1.5)],
        )
    ]
    save_track(track)
    tags = ID3(mp3)
    tags.getall("SYLT")[0].text = [("\nHello", 500), (" world", 1300)]
    tags.save(mp3)
    reopened = read_track(mp3)
    assert [word.start for word in reopened.aligned_lines[0].words] == [0.5, 1.3]
    assert all(word.source == "estimated" for word in reopened.aligned_lines[0].words)
