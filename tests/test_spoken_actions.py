"""Unit tests for wake-word voice commands ("jarvis <command>" -> keystroke)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import (
    segment_spoken_actions,
    should_skip_cleanup,
    DEFAULT_FAST_MODE_MAX_WORDS,
    DEFAULT_WAKE_WORD,
    _KEY_ENTER,
    _KEY_TAB,
    _KEY_ESC,
    _KEY_A,
    _KEY_Z,
    _KEY_BACKSPACE,
    _KEY_LEFTCTRL,
    _KEY_LEFTSHIFT,
    _tap,
    _chord,
)


def kinds(segs):
    return [s[0] for s in segs]


def texts(segs):
    return [s[1] for s in segs if s[0] == "text"]


def keyseqs(segs):
    return [s[1] for s in segs if s[0] == "key"]


class TestNoCommand:
    @pytest.mark.unit
    def test_plain_text_untouched(self):
        text = "the quick brown fox jumps over the lazy dog"
        assert segment_spoken_actions(text) == [("text", text)]

    @pytest.mark.unit
    def test_empty(self):
        assert segment_spoken_actions("") == [("text", "")]

    @pytest.mark.unit
    def test_bare_command_without_wake_word_stays_text(self):
        # The whole point: "new line" / "enter" without the wake word are text.
        for t in ["new line", "press enter", "select all", "scratch that idea"]:
            assert segment_spoken_actions(t) == [("text", t)]

    @pytest.mark.unit
    def test_wake_word_alone_is_text(self):
        assert segment_spoken_actions("jarvis") == [("text", "jarvis")]

    @pytest.mark.unit
    def test_wake_word_not_followed_by_command_is_text(self):
        text = "jarvis is a marvel character"
        assert segment_spoken_actions(text) == [("text", text)]

    @pytest.mark.unit
    def test_natural_sentence_with_command_words_is_text(self):
        text = "remember the data you enter and select all the rows"
        assert segment_spoken_actions(text) == [("text", text)]


class TestSingleCommands:
    @pytest.mark.unit
    def test_enter(self):
        segs = segment_spoken_actions("jarvis enter")
        assert kinds(segs) == ["key"]
        assert keyseqs(segs)[0] == [_tap(_KEY_ENTER)]

    @pytest.mark.unit
    def test_new_line(self):
        assert keyseqs(segment_spoken_actions("jarvis new line"))[0] == [_tap(_KEY_ENTER)]

    @pytest.mark.unit
    def test_new_paragraph_two_enters(self):
        assert keyseqs(segment_spoken_actions("jarvis new paragraph"))[0] == [_tap(_KEY_ENTER), _tap(_KEY_ENTER)]

    @pytest.mark.unit
    def test_send_it_is_enter(self):
        assert keyseqs(segment_spoken_actions("jarvis send it"))[0] == [_tap(_KEY_ENTER)]

    @pytest.mark.unit
    def test_tab(self):
        assert keyseqs(segment_spoken_actions("jarvis tab"))[0] == [_tap(_KEY_TAB)]

    @pytest.mark.unit
    def test_escape(self):
        assert keyseqs(segment_spoken_actions("jarvis escape"))[0] == [_tap(_KEY_ESC)]

    @pytest.mark.unit
    def test_select_all(self):
        assert keyseqs(segment_spoken_actions("jarvis select all"))[0] == [_chord(_KEY_LEFTCTRL, _KEY_A)]

    @pytest.mark.unit
    def test_undo(self):
        assert keyseqs(segment_spoken_actions("jarvis undo"))[0] == [_chord(_KEY_LEFTCTRL, _KEY_Z)]

    @pytest.mark.unit
    def test_undo_that_consumes_that(self):
        # "undo that" must win over "undo" so "that" isn't left as text.
        segs = segment_spoken_actions("jarvis undo that")
        assert kinds(segs) == ["key"]
        assert keyseqs(segs)[0] == [_chord(_KEY_LEFTCTRL, _KEY_Z)]

    @pytest.mark.unit
    def test_redo(self):
        assert keyseqs(segment_spoken_actions("jarvis redo"))[0] == [_chord(_KEY_LEFTCTRL, _KEY_LEFTSHIFT, _KEY_Z)]

    @pytest.mark.unit
    def test_delete_word(self):
        assert keyseqs(segment_spoken_actions("jarvis delete word"))[0] == [_chord(_KEY_LEFTCTRL, _KEY_BACKSPACE)]

    @pytest.mark.unit
    def test_scratch_that_is_scratch_segment(self):
        segs = segment_spoken_actions("jarvis scratch that")
        assert kinds(segs) == ["scratch"]

    @pytest.mark.unit
    def test_case_insensitive(self):
        assert kinds(segment_spoken_actions("JARVIS Enter")) == ["key"]


class TestWakeWordConfig:
    @pytest.mark.unit
    def test_custom_wake_word(self):
        segs = segment_spoken_actions("computer enter", wake_word="computer")
        assert kinds(segs) == ["key"]

    @pytest.mark.unit
    def test_default_wake_inactive_when_custom_set(self):
        # With a custom wake word, the default "jarvis enter" is just text.
        assert segment_spoken_actions("jarvis enter", wake_word="computer") == [("text", "jarvis enter")]

    @pytest.mark.unit
    @pytest.mark.parametrize("variant", ["cortex", "oflo", "o flow", "oh flow", "off flow"])
    def test_cortex_fuzzy_variants_when_configured(self, variant):
        # "cortex" is a coined word Whisper scatters, so it keeps fuzzy variants
        # when a user explicitly chooses it.
        assert kinds(segment_spoken_actions(f"{variant} enter", wake_word="cortex")) == ["key"]


class TestInterleaved:
    @pytest.mark.unit
    def test_command_between_text(self):
        segs = segment_spoken_actions("go to settings jarvis new line click save")
        assert kinds(segs) == ["text", "key", "text"]
        assert texts(segs) == ["go to settings", "click save"]

    @pytest.mark.unit
    def test_trailing_command_trims_space(self):
        segs = segment_spoken_actions("save the file jarvis enter")
        assert kinds(segs) == ["text", "key"]
        assert texts(segs) == ["save the file"]

    @pytest.mark.unit
    def test_orphan_punctuation_after_command_dropped(self):
        segs = segment_spoken_actions("do it jarvis enter.")
        assert kinds(segs) == ["text", "key"]
        assert texts(segs) == ["do it"]

    @pytest.mark.unit
    def test_multiple_commands(self):
        segs = segment_spoken_actions("first jarvis new line second jarvis new line third")
        assert kinds(segs) == ["text", "key", "text", "key", "text"]
        assert texts(segs) == ["first", "second", "third"]

    @pytest.mark.unit
    def test_leading_command(self):
        segs = segment_spoken_actions("jarvis tab username")
        assert kinds(segs) == ["key", "text"]
        assert texts(segs) == ["username"]

    @pytest.mark.unit
    def test_comma_after_wake_word_still_fires(self):
        # Cleanup LLM often writes "Jarvis, scratch that".
        assert kinds(segment_spoken_actions("Jarvis, scratch that")) == ["scratch"]

    @pytest.mark.unit
    def test_period_after_wake_does_not_cross_sentence(self):
        # "...thanks Jarvis. Select all of them..." must NOT fire select-all.
        text = "thanks Jarvis. Select all of them are great"
        assert segment_spoken_actions(text) == [("text", text)]


class TestFastModeCleanupSkip:
    @pytest.mark.unit
    def test_short_text_skips(self):
        assert should_skip_cleanup("open the pull request", 8) is True

    @pytest.mark.unit
    def test_exactly_threshold_skips(self):
        assert should_skip_cleanup("one two three four five six seven eight", 8) is True

    @pytest.mark.unit
    def test_over_threshold_cleans(self):
        assert should_skip_cleanup("one two three four five six seven eight nine", 8) is False

    @pytest.mark.unit
    def test_zero_disables_fast_mode(self):
        assert should_skip_cleanup("hi", 0) is False

    @pytest.mark.unit
    def test_negative_disables_fast_mode(self):
        assert should_skip_cleanup("hi", -1) is False

    @pytest.mark.unit
    def test_empty_text_skips(self):
        assert should_skip_cleanup("", 8) is True

    @pytest.mark.unit
    def test_defaults_are_sane(self):
        assert DEFAULT_FAST_MODE_MAX_WORDS == 8
        assert DEFAULT_WAKE_WORD == "jarvis"
