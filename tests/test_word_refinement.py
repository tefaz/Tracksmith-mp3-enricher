from copy import deepcopy

import numpy as np
import pytest

from song_metadata_enricher.jobs import Cancelled, JobContext
from song_metadata_enricher.model import Word
from song_metadata_enricher.word_refinement import refine_starts


def test_refinement_finds_acoustic_onset_without_moving_line_or_sustained_ends():
    audio = np.zeros(16000, dtype=np.float32)
    audio[7000:9000] = 1  # Second word starts acoustically at 0.4375 seconds.
    words = [Word("first", 0, 0.2, 0.9), Word("second", 0.2, 0.8, 0.9)]
    original = deepcopy(words)

    def score(batch):
        return np.array([[0.9, 0.9 if np.sum(crop[7000:9000]) >= 1950 else 0.1] for crop in batch])

    result = refine_starts(words, 0, score, audio, [0, 1, 2], JobContext())
    assert 0.40 < result[1].start < 0.45
    assert result[0] == original[0]
    assert [word.end for word in result] == [word.end for word in original]
    assert words == original
    assert result[1].source == "ai"


def test_weak_words_are_not_trimmed_and_refinement_is_cancellable():
    audio = np.ones(16000, dtype=np.float32)
    words = [Word("first", 0, 0.2, 0.9), Word("uncertain", 0.2, 0.8, 0.2)]
    def score(batch):
        return np.full((len(batch), 2), 0.9)
    assert refine_starts(words, 0, score, audio, [0, 1, 2], JobContext()) == words
    context = JobContext()
    context.cancelled.set()
    with pytest.raises(Cancelled):
        refine_starts(words, 0, score, audio, [0, 1, 2], context)
