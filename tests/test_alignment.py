import numpy as np
import pytest

from song_metadata_enricher.alignment import LocalAlignment, ctc_viterbi, match_passages
from song_metadata_enricher.config import Settings
from song_metadata_enricher.jobs import JobContext
from song_metadata_enricher.model import LyricLine, Word
from song_metadata_enricher.tags import read_track


def recognized(text, start):
    return [
        Word(word, start + i * 0.3, start + i * 0.3 + 0.25, 0.99)
        for i, word in enumerate(text.split())
    ]


def test_omitted_verse_and_chronological_repeats():
    lines = [
        LyricLine("an absent introductory verse"),
        LyricLine("here comes the sun"),
        LyricLine("an absent second verse"),
        LyricLine("here comes the sun"),
    ]
    words = recognized("here comes the sun", 0) + recognized("here comes the sun", 10)
    matches = match_passages(lines, words)
    assert set(matches) == {1, 3}
    assert matches[1].first == 0
    assert matches[3].first == 4


def test_repeated_lines_do_not_reuse_one_occurrence():
    lines = [LyricLine("here comes the sun") for _ in range(3)]
    matches = match_passages(lines, recognized("here comes the sun", 1))
    assert len(matches) == 1


def test_reordered_sections_require_review():
    lines = [LyricLine("first passage has words"), LyricLine("second passage is different")]
    words = recognized("second passage is different", 1) + recognized("first passage has words", 10)
    matches = match_passages(lines, words)
    assert set(matches) == {0, 1}
    assert any(passage.reordered and passage.score <= 0.65 for passage in matches.values())


def test_no_fabricated_timing_from_dissimilar_words():
    assert (
        match_passages(
            [LyricLine("hier kommt die sonne")], recognized("completely different audio words", 0)
        )
        == {}
    )


def test_ctc_repeated_letters_require_blank():
    # blank=0, L=1. "LL" requires two distinct token runs separated by blank.
    posterior = np.array([[0.99, 0.01], [0.01, 0.99], [0.99, 0.01], [0.01, 0.99], [0.99, 0.01]])
    spans = ctc_viterbi(np.log(posterior), [1, 1], blank=0)
    assert spans[0][:2] == (1, 2)
    assert spans[1][:2] == (3, 4)
    assert spans[0][2] == pytest.approx(0.99)
    with pytest.raises(ValueError):
        ctc_viterbi(np.log(np.array([[0.1, 0.9], [0.1, 0.9]])), [1, 1], 0)


def test_mocked_pipeline_decodes_real_local_mp3(mp3, tmp_path):
    track = read_track(mp3)
    track.aligned_lines = [LyricLine("here comes the sun"), LyricLine("this verse was cut")]

    class Transcription:
        def transcribe(self, audio, context):
            assert audio.dtype == np.float32
            assert 63000 < len(audio) < 66000
            return recognized("here comes the sun", 0.3), "en"

    class Forced:
        def words(self, audio, text, offset):
            assert text == "here comes the sun"
            return recognized(text, offset + 0.1)

    result = LocalAlignment(
        Settings(cache_directory=str(tmp_path / "cache")), Transcription(), Forced()
    ).align(track, JobContext())
    assert result[0].start is not None
    assert len(result[0].words) == 4
    assert result[1].start is None
    assert track.aligned_lines[0].start is None


def test_weak_acoustic_support_withholds_timestamps(mp3, tmp_path):
    track = read_track(mp3)
    track.aligned_lines = [LyricLine("here comes the sun")]

    class Transcription:
        def transcribe(self, audio, context):
            return recognized("here comes the sun", 0.4), "en"

    class Forced:
        def words(self, audio, text, offset):
            return [
                Word(word.text, word.start, word.end, 0.1)
                for word in recognized(text, offset + 0.1)
            ]

    result = LocalAlignment(
        Settings(cache_directory=str(tmp_path / "cache")), Transcription(), Forced()
    ).align(track, JobContext())
    assert result[0].start is None
    assert result[0].confidence == 0.1


def test_singing_is_not_discarded_by_speech_detection(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from song_metadata_enricher.alignment import WhisperTranscription

    options = []

    class Model:
        def transcribe(self, audio, **kwargs):
            options.append(kwargs)
            return {
                "language": "en",
                "segments": [
                    {
                        "no_speech_prob": 0.95,
                        "words": [
                            {"word": "Sung", "start": 1, "end": 1.3, "probability": 0.97},
                            {"word": "words", "start": 1.3, "end": 2, "probability": 0.94},
                        ],
                    }
                ],
            }

    monkeypatch.setitem(
        sys.modules, "whisper", SimpleNamespace(load_model=lambda *args, **kwargs: Model())
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    words, language = WhisperTranscription(
        Settings(cache_directory=str(tmp_path)), "cpu"
    ).transcribe(np.zeros(48000), JobContext())
    assert [word.text for word in words] == ["Sung", "words"]
    assert language == "en"
    assert options[0]["no_speech_threshold"] is None
    assert "initial_prompt" not in options[0]  # Recognition remains independent evidence.


def test_known_text_grouping_uses_audio_order_and_distinct_repeats():
    from song_metadata_enricher.alignment import Passage, group_passages

    words = (
        recognized("first chorus words here", 1)
        + recognized("second chorus words here", 7)
        + recognized("later chorus words here", 40)
    )
    passages = {5: Passage(0, 4, 0.95, True), 1: Passage(4, 8, 0.95), 8: Passage(8, 12, 0.95)}
    assert group_passages(passages, words) == [[5], [1], [8]]  # >3s instrumental gaps.
    words = recognized("first chorus words here", 1) + recognized("second chorus words here", 3)
    assert group_passages({5: Passage(0, 4, 0.95, True), 1: Passage(4, 8, 0.95)}, words) == [[5, 1]]


def test_grouped_known_lyrics_keep_boundaries_annotations_and_repeats():
    from song_metadata_enricher.alignment import Passage, WhisperForcedAligner
    from song_metadata_enricher.lyrics import alignment_text

    lines = [
        LyricLine("Here Comes the Sun (x2)"),
        LyricLine("This verse was cut"),
        LyricLine("Here Comes the Sun"),
    ]
    words = recognized("here comes the sun", 1) + recognized("here comes the sun", 12)
    passages = {0: Passage(0, 4, 0.99), 2: Passage(4, 8, 0.99)}
    aligner = WhisperForcedAligner.__new__(WhisperForcedAligner)
    references = []

    def force(audio, text, offset):
        references.append(text)
        assert "Here Comes" in text  # Case survives; it matters for model token scores.
        assert "x2" not in text
        assert "cut" not in text
        return recognized(alignment_text(text), offset + 0.1)

    aligner.words = force
    result = aligner.align_lines(np.zeros(16 * 16000), lines, passages, words, JobContext())
    assert set(result) == {0, 2}
    assert len(result[0]) == len(result[2]) == 4
    assert result[0][0].start < result[2][0].start
    assert len(references) == 2


def mock_live_backend(monkeypatch, tmp_path, recognized_words):
    import song_metadata_enricher.alignment as module

    calls = {"decode": 0, "transcribe": 0}
    monkeypatch.setattr(module.importlib.metadata, "version", lambda package: "test")
    monkeypatch.setattr(module, "select_device", lambda device: "cpu")

    def decode(*args):
        calls["decode"] += 1
        return np.zeros(4 * 16000)

    class Transcriber:
        def __init__(self, *args):
            pass

        def transcribe(self, audio, context):
            calls["transcribe"] += 1
            return recognized_words, "en"

    class Forced:
        def __init__(self, *args):
            pass

        def words(self, audio, text, offset):
            return recognized(text, offset + 0.1)

    monkeypatch.setattr(module, "decode_audio", decode)
    monkeypatch.setattr(module, "WhisperTranscription", Transcriber)
    monkeypatch.setattr(module, "WhisperForcedAligner", Forced)
    return LocalAlignment(Settings(cache_directory=str(tmp_path / "cache"))), calls


def test_failed_cached_alignment_does_not_look_like_instant_success(mp3, tmp_path, monkeypatch):
    track = read_track(mp3)
    track.aligned_lines = [LyricLine("here comes the sun")]
    backend, calls = mock_live_backend(monkeypatch, tmp_path, [])
    messages = []
    context = JobContext(lambda text, percent: messages.append(text))
    assert backend.align(track, context)[0].start is None
    messages.clear()
    assert backend.align(track, context)[0].start is None
    assert calls["decode"] == 2
    assert not any("cached alignment" in message for message in messages)


def test_successful_cache_is_explicit_and_fresh_analysis_recomputes_audio(
    mp3, tmp_path, monkeypatch
):
    track = read_track(mp3)
    track.aligned_lines = [LyricLine("here comes the sun")]
    backend, calls = mock_live_backend(monkeypatch, tmp_path, recognized("here comes the sun", 0.3))
    messages = []
    context = JobContext(lambda text, percent: messages.append(text))
    assert backend.align(track, context)[0].start is not None
    assert backend.align(track, context)[0].start is not None
    assert calls == {"decode": 1, "transcribe": 1}
    assert any("cached alignment" in message for message in messages)
    assert backend.align(track, context, fresh=True)[0].start is not None
    assert calls == {"decode": 2, "transcribe": 2}


def test_refined_alignment_uses_word_pass_without_changing_outer_bounds(monkeypatch):
    import song_metadata_enricher.word_refinement as refinement
    from song_metadata_enricher.alignment import Passage, WhisperForcedAligner

    lines = [LyricLine("Here comes the sun")]
    evidence = recognized("here comes the sun", 1)
    aligner = WhisperForcedAligner.__new__(WhisperForcedAligner)
    aligner.words = lambda audio, text, offset: recognized(text, offset + 0.1)
    aligner.refine_words = True
    aligner.model = object()
    aligner.tokenizer = object()
    calls = []

    def refine(model, tokenizer, audio, words, offset, context):
        from copy import deepcopy
        calls.append((len(audio), offset))
        result = deepcopy(words)
        result[1].start += 0.05
        return result

    monkeypatch.setattr(refinement, "refine_whisper_starts", refine)
    result = aligner.align_lines(np.zeros(4 * 16000), lines, {0: Passage(0, 4, 0.99)}, evidence, JobContext())[0]
    assert len(calls) == 1
    assert result[0].start == pytest.approx(0.6)
    assert result[1].start == pytest.approx(0.95)
    assert result[-1].end == pytest.approx(1.75)
