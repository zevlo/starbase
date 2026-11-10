"""sanitize_answer: CAPCOM's deterministic guard against leaked reasoning
tags and the retired disclosure line. The Mir case below is the exact leak
that motivated the module."""

from sanitize import sanitize_answer

DISCLOSURE = "(general knowledge — not from starbase's live data)"


def test_strips_thinking_block_and_disclosure():
    raw = (
        "<thinking>The User has asked for a general knowledge explanation about a space "
        "vehicle, which is outside the scope of the available tools. I can provide this "
        "information based on my own knowledge.</thinking>\n"
        "The Mir was a Soviet/Russian modular space station, operational from 1986 to 2001. "
        f"It was the first long-term, continuously inhabited space station. {DISCLOSURE}"
    )
    assert sanitize_answer(raw) == (
        "The Mir was a Soviet/Russian modular space station, operational from 1986 to 2001. "
        "It was the first long-term, continuously inhabited space station."
    )


def test_strips_multiline_thinking_block():
    raw = "<thinking>\nline one\nline two\n</thinking>\nFalcon 9 is a reusable rocket."
    assert sanitize_answer(raw) == "Falcon 9 is a reusable rocket."


def test_strips_stray_unclosed_tags():
    assert sanitize_answer("<thinking> partial thought") == "partial thought"
    assert sanitize_answer("answer </thinking>") == "answer"


def test_strips_disclosure_mid_answer_and_variants():
    assert sanitize_answer(f"Part one. {DISCLOSURE} Part two.") == "Part one. Part two."
    curly = "(general knowledge — not from starbase’s live data)"
    assert sanitize_answer(f"Answer {curly}") == "Answer"
    hyphen = "(General knowledge - not from starbase's live data)"
    assert sanitize_answer(hyphen) == ""


def test_keeps_normal_answer_untouched_and_is_idempotent():
    plain = "A scrub means the launch was paused before flight.\nSecond line stays."
    assert sanitize_answer(plain) == plain
    assert sanitize_answer(sanitize_answer(plain)) == plain
    assert sanitize_answer("a < b and 9 < 10") == "a < b and 9 < 10"


def test_thinking_only_collapses_to_empty():
    assert sanitize_answer("<thinking>only reasoning</thinking>") == ""
    assert sanitize_answer("  \n  ") == ""
