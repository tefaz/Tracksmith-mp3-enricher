from dataclasses import asdict

import pytest

from tracksmith.cache import Cache
from tracksmith.jobs import JobContext
from tracksmith.providers import HttpClient, LRCLibProvider, MusicBrainzProvider


class StubHttp:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def json(self, url, context):
        self.urls.append(url)
        return self.response


def test_lrclib_uses_plain_lyrics_only():
    http = StubHttp(
        [
            {
                "trackName": "Song",
                "artistName": "Artist",
                "plainLyrics": "first line",
                "syncedLyrics": "[00:25.00]first line",
            }
        ]
    )
    candidate = LRCLibProvider(http).search("Artist", "Song", JobContext())[0]
    assert candidate.lyrics == "first line"
    assert "syncedLyrics" not in asdict(candidate)


def test_musicbrainz_corrected_spelling_candidates():
    http = StubHttp(
        {
            "recordings": [
                {
                    "id": "recording",
                    "title": "Sonne",
                    "artist-credit": [{"name": "Rammstein"}],
                    "releases": [
                        {"id": "original", "title": "Mutter", "date": "2001"},
                        {"id": "remaster", "title": "Mutter Remastered", "date": "2025"},
                    ],
                }
            ]
        }
    )
    candidates = MusicBrainzProvider(http).search("Rammstien", "Sone", JobContext())
    assert len(candidates) == 2
    assert candidates[0].metadata.artist == "Rammstein"
    assert candidates[0].metadata.title == "Sonne"
    assert 0.7 < candidates[0].confidence < 1
    assert candidates[0].release_id != candidates[1].release_id


def test_cover_search_browses_all_pages_and_prefers_original_studio_album():
    from urllib.parse import parse_qs, urlparse

    def release(identity, album_type, date, secondary=(), status="Official"):
        return {
            "id": identity,
            "title": identity,
            "date": date,
            "status": status,
            "artist-credit": [{"name": "Artist"}],
            "release-group": {
                "primary-type": album_type,
                "secondary-types": secondary,
                "first-release-date": date,
            },
        }

    class PagedHttp:
        def __init__(self):
            self.offsets = []

        def json(self, url, context):
            if "/recording/" in url:
                return {
                    "recordings": [
                        {
                            "id": "recording",
                            "title": "Song",
                            "artist-credit": [{"name": "Artist"}],
                            "releases": [{"id": "snippet", "title": "Misleading search snippet"}],
                        }
                    ]
                }
            query = parse_qs(urlparse(url).query)
            assert query["recording"] == ["recording"]
            assert query["inc"] == ["release-groups+artist-credits"]
            offset = int(query["offset"][0])
            self.offsets.append(offset)
            return {
                "release-count": 5,
                "releases": (
                    [
                        release("hits", "Album", "1999", ["Compilation"]),
                        release("single", "Single", "2000"),
                    ]
                    if offset == 0
                    else [
                        release("original", "Album", "2001"),
                        release("reissue", "Album", "2025"),
                        release("bootleg", "Album", "1998", status="Bootleg"),
                    ]
                ),
            }

    http = PagedHttp()
    results = MusicBrainzProvider(http).search_covers("Artist", "Song", JobContext())
    assert http.offsets == [0, 2]
    assert [c.release_id for c in results] == ["original", "reissue", "single", "hits", "bootleg"]
    assert results[0].metadata.album_artist == "Artist"
    assert "First released 2001" in results[0].note


def test_exact_song_title_does_not_accept_wrong_artist():
    http = StubHttp(
        {
            "recordings": [
                {
                    "id": "wrong",
                    "title": "Song",
                    "artist-credit": [{"name": "Someone Else"}],
                    "releases": [{"id": "wrong-album", "title": "Wrong Album"}],
                }
            ]
        }
    )
    assert MusicBrainzProvider(http).search("Artist", "Song", JobContext()) == []


def test_cover_search_inspects_studio_recording_ahead_of_equal_live_matches():
    from urllib.parse import parse_qs, urlparse

    class Http:
        def __init__(self):
            self.recordings = []

        def json(self, url, context):
            query = parse_qs(urlparse(url).query)
            if "/recording/" in url:
                assert query["limit"] == ["100"]
                return {
                    "recordings": [
                        {
                            "id": str(i),
                            "title": "Song",
                            "disambiguation": "live",
                            "artist-credit": [{"name": "Artist"}],
                            "releases": [
                                {
                                    "title": "Live",
                                    "release-group": {
                                        "primary-type": "Album",
                                        "secondary-types": ["Live"],
                                    },
                                }
                            ],
                        }
                        for i in range(25)
                    ]
                    + [
                        {
                            "id": "studio",
                            "title": "Song",
                            "artist-credit": [{"name": "Artist"}],
                            "releases": [
                                {
                                    "title": "Original",
                                    "release-group": {
                                        "primary-type": "Album",
                                        "secondary-types": [],
                                    },
                                }
                            ],
                        }
                    ]
                }
            recording = query["recording"][0]
            self.recordings.append(recording)
            return {
                "releases": [
                    {
                        "id": recording,
                        "title": recording,
                        "release-group": {"primary-type": "Album"},
                    }
                ]
            }

    http = Http()
    results = MusicBrainzProvider(http).search_covers("Artist", "Song", JobContext())
    assert http.recordings[0] == "studio"
    assert results[0].release_id == "studio"


def test_http_cache_prevents_repeated_network(tmp_path, monkeypatch):
    http = HttpClient(Cache(tmp_path))
    calls = []

    def download(url, context):
        calls.append(url)
        return b'{"result": 1}'

    monkeypatch.setattr(http, "bytes", download)
    assert http.json("https://example.org/lookup", JobContext()) == {"result": 1}
    assert http.json("https://example.org/lookup", JobContext()) == {"result": 1}
    assert len(calls) == 1


def test_musicbrainz_uses_bounded_prefix_fallback():
    class FallbackHttp:
        def __init__(self):
            self.urls = []

        def json(self, url, context):
            self.urls.append(url)
            if len(self.urls) < 3:
                return {"recordings": []}
            return {
                "recordings": [
                    {"id": "song", "title": "Sonne", "artist-credit": [{"name": "Rammstein"}]}
                ]
            }

    http = FallbackHttp()
    candidates = MusicBrainzProvider(http).search("Rammstien", "Sone", JobContext())
    assert candidates[0].metadata.title == "Sonne"
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(http.urls[-1]).query)["query"][0]
    assert query == "recording:(so*) AND artist:(ramm*)"


def test_releases_offer_album_and_single():
    from tracksmith.model import Candidate, Metadata

    http = StubHttp(
        {
            "title": "Song",
            "artist-credit": [{"name": "Artist"}],
            "releases": [
                {"id": "album", "title": "Original Album", "date": "2001", "status": "Official"},
                {"id": "single", "title": "Single", "date": "2000", "status": "Official"},
            ],
        }
    )
    result = MusicBrainzProvider(http).releases(
        Candidate(Metadata(title="Song"), 0.9, "MusicBrainz", "recording"), JobContext()
    )
    assert {candidate.release_id for candidate in result} == {"album", "single"}
    assert result[0].metadata.album == "Single"


def test_cover_404_is_cached_but_temporary_network_failure_is_not(tmp_path, monkeypatch):
    from tracksmith.providers import CoverArtProvider

    http = HttpClient(Cache(tmp_path))
    calls = []

    def missing(url, context):
        calls.append(url)
        raise RuntimeError("Provider returned HTTP 404")

    monkeypatch.setattr(http, "bytes", missing)
    provider = CoverArtProvider(http)
    for _ in range(2):
        with pytest.raises(ValueError, match="no front cover"):
            provider.fetch("missing", JobContext())
    assert len(calls) == 1

    def offline(url, context):
        calls.append(url)
        raise RuntimeError("Provider could not be reached")

    monkeypatch.setattr(http, "bytes", offline)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="could not be reached"):
            provider.fetch("temporary", JobContext())
    assert len(calls) == 3


def test_merge_keeps_other_recordings_and_editions_without_duplicate_rows():
    from tracksmith.model import Candidate, Metadata
    from tracksmith.providers import merge_release_candidates

    original = Candidate(Metadata(album="Album"), 0.9, "test", "recording", "release")
    stronger = Candidate(Metadata(album="Album"), 0.95, "test", "recording", "release")
    other_recording = Candidate(Metadata(album="Album"), 0.9, "test", "other", "release")
    other_edition = Candidate(Metadata(album="Album"), 0.9, "test", "recording", "edition")
    result = merge_release_candidates([original, other_recording], [stronger, other_edition])
    assert result == [stronger, other_recording, other_edition]
