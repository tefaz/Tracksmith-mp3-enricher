import pytest

from tracksmith.embedded_timing import sylt_cues, sylt_lines
from tracksmith.model import LyricLine, Word


@pytest.mark.parametrize(
    "text, words",
    [
        ("Hello,  world!", ["Hello", "world"]),
        ("[Chorus] Don’t stop (x2)", ["Don't", "stop"]),
        ("Café — déjà vu!", ["Café", "déjà", "vu"]),
        ("Hello [quietly] world", ["Hello", "world"]),
        ("Never-never stop", ["never-never", "stop"]),
    ],
)
def test_word_cues_preserve_full_line_text_and_timestamps(text, words):
    line = LyricLine(
        text, 0.5, words=[Word(word, 0.5 + index, 0.9 + index) for index, word in enumerate(words)]
    )
    cues = sylt_cues([line])
    assert "".join(fragment for fragment, _ in cues) == "\n" + text
    assert [timestamp for _, timestamp in cues] == [
        500 + index * 1000 for index in range(len(words))
    ]
    assert sylt_lines(cues, 5)[0].text == text
    assert sylt_lines(cues, 5)[0].start == 0.5


def test_cues_cannot_move_a_word_into_the_next_line():
    lines = [
        LyricLine("Hello world", 0.5, words=[Word("Hello", 0.5, 0.8), Word("world", 2, 2.4)]),
        LyricLine("Next line", 1.8),
    ]
    with pytest.raises(ValueError, match="cross the following line"):
        sylt_cues(lines)


def test_empty_fragments_do_not_create_invalid_words():
    lines = sylt_lines([("\n", 500), (" ", 700), ("\nActual line", 1000)], 4)
    assert not lines[0].words
    assert lines[1].text == "Actual line"
