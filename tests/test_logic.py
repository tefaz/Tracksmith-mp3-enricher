import pytest

from song_metadata_enricher.cache import Cache, file_hash
from song_metadata_enricher.config import Settings
from song_metadata_enricher.lyrics import (
    alignment_text,
    format_timestamp,
    generate_lrc,
    parse_timestamp,
    split_lyrics,
)
from song_metadata_enricher.model import LyricLine, active_line
from song_metadata_enricher.providers import parse_filename, prepare_artwork


@pytest.mark.parametrize(
    "filename,artist,title",
    [
        ("Rammstien - Sone edited.mp3", "Rammstien", "Sone"),
        ("01. Artist_Name — A Title (edited version).mp3", "Artist Name", "A Title"),
        ("Artist - Song - Live.mp3", "Artist", "Song - Live"),
        ("Song.mp3", "", "Song"),
        ("AC-DC - Thunderstruck.mp3", "AC-DC", "Thunderstruck"),
    ],
)
def test_filename(filename, artist, title):
    assert parse_filename(filename) == (artist, title)


def test_normalization_retains_display_and_repeats():
    original = "[Verse 1]\nHier kommt die Sonne!\n\n[Chorus]\nHier kommt die Sonne! (x2)\n"
    lines = split_lyrics(original)
    assert len(lines) == 2
    assert alignment_text(lines[0].text) == alignment_text(lines[1].text) == "hier kommt die sonne"
    assert lines[1].text.endswith("(x2)")
    assert original.startswith("[Verse 1]")


def test_repeated_lines_preserve_distinct_timing():
    lines = [LyricLine("chorus", 1.123), LyricLine("verse", 2), LyricLine("chorus", 3.456)]
    result = split_lyrics("chorus\nnew verse\nchorus", lines)
    assert [line.start for line in result] == [1.123, None, 3.456]
    assert result[0] is not result[2]


@pytest.mark.parametrize(
    "seconds,text",
    [(0, "00:00.000"), (12.142, "00:12.142"), (60, "01:00.000"), (6123.007, "102:03.007")],
)
def test_timestamps(seconds, text):
    assert format_timestamp(seconds) == text
    assert parse_timestamp(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["00:60.000", "-01:00.000", "nan", "12:10.9999", "hello"])
def test_invalid_timestamp(text):
    with pytest.raises(ValueError):
        parse_timestamp(text)


def test_lrc_order_precision_and_omissions():
    lines = [LyricLine("second", 60), LyricLine("missing"), LyricLine("first", 59.995)]
    assert generate_lrc(lines) == "[01:00.00]first\n[01:00.00]second\n"
    assert lines[2].start == 59.995


def test_active_line_unsorted_and_end_of_vocals():
    lines = [LyricLine("late", 5, 7), LyricLine("missing"), LyricLine("early", 1, 3)]
    assert active_line(lines, 0) is None
    assert active_line(lines, 2) == 2
    assert active_line(lines, 4) is None
    assert active_line(lines, 6) == 0


def test_cache_invalidation(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"first")
    cache = Cache(tmp_path / "cache")
    first = {"file": file_hash(path), "model": "small", "lyrics": "hello"}
    cache.put("alignment", first, [1, 2])
    assert cache.get("alignment", first) == [1, 2]
    path.write_bytes(b"second")
    assert cache.get("alignment", {**first, "file": file_hash(path)}) is None
    assert cache.get("alignment", {**first, "model": "base"}) is None
    assert cache.get("alignment", {**first, "lyrics": "different"}) is None
    cache.key("alignment", first).write_text("broken json")
    assert cache.get("alignment", first) is None


def test_settings_roundtrip(tmp_path):
    settings = Settings(language="de", device="cpu")
    path = tmp_path / "settings.json"
    settings.save(path)
    assert Settings.load(path) == settings
    assert path.stat().st_mode & 0o777 == 0o600


def test_artwork_resize():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (2000, 1200), "blue")
    data = BytesIO()
    image.save(data, "PNG")
    art = prepare_artwork(data.getvalue())
    with Image.open(BytesIO(art.data)) as result:
        assert result.size == (1000, 600)
    assert art.mime == "image/jpeg"


def test_timestamp_edit_invalidates_words():
    from song_metadata_enricher.model import Word

    line = LyricLine("test", 1, 2, 0.9, "ai", [Word("test", 1, 2)])
    line.set_timestamp(1.234, 4)
    assert not line.words
    assert line.source == "manual"
    with pytest.raises(ValueError):
        line.set_timestamp(float("nan"), 4)


def test_legacy_backup_setting_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"backup": true, "language": "da"}')
    settings = Settings.load(path)
    assert settings.language == "da"
    assert not hasattr(settings, "backup")
    settings.save(path)
    assert '"backup"' not in path.read_text()
