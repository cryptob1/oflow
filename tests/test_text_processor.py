"""Unit tests for TextProcessor (spoken punctuation and word replacements)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oflow import TextProcessor


class TestSpokenPunctuation:
    """Tests for spoken punctuation feature."""

    @pytest.mark.unit
    def test_basic_punctuation(self):
        """Basic punctuation marks should be converted."""
        processor = TextProcessor(enable_punctuation=True)

        assert processor.process("hello comma world") == "hello, world"
        assert processor.process("hello period") == "hello."
        assert processor.process("what is this question mark") == "what is this?"
        assert processor.process("wow exclamation mark") == "wow!"

    @pytest.mark.unit
    def test_parentheses_and_brackets(self):
        """Parentheses and brackets should work."""
        processor = TextProcessor(enable_punctuation=True)

        assert processor.process("test open paren content close paren") == "test (content)"
        assert processor.process("array open bracket one close bracket") == "array [one]"

    @pytest.mark.unit
    def test_new_line_and_paragraph(self):
        """New line and paragraph commands should work."""
        processor = TextProcessor(enable_punctuation=True)

        assert processor.process("line one new line line two") == "line one\nline two"
        assert processor.process("para one new paragraph para two") == "para one\n\npara two"

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Punctuation should be case insensitive."""
        processor = TextProcessor(enable_punctuation=True)

        assert processor.process("hello COMMA world") == "hello, world"
        assert processor.process("test Period") == "test."

    @pytest.mark.unit
    def test_disabled_punctuation(self):
        """When disabled, punctuation should not be converted."""
        processor = TextProcessor(enable_punctuation=False)

        assert processor.process("hello comma world") == "hello comma world"
        assert processor.process("test period") == "test period"

    @pytest.mark.unit
    def test_spacing_cleanup(self):
        """Spacing around punctuation should be cleaned up."""
        processor = TextProcessor(enable_punctuation=True)

        # Space before closing punctuation should be removed
        result = processor.process("hello comma world period")
        assert result == "hello, world."
        assert " ," not in result
        assert " ." not in result


class TestWordReplacements:
    """Tests for custom word replacement feature."""

    @pytest.mark.unit
    def test_basic_replacement(self):
        """Basic word replacement should work."""
        processor = TextProcessor(enable_punctuation=False, replacements={"oflow": "Oflow"})

        assert processor.process("test oflow here") == "test Oflow here"

    @pytest.mark.unit
    def test_multiple_replacements(self):
        """Multiple replacements should work."""
        processor = TextProcessor(
            enable_punctuation=False,
            replacements={"whisper": "Whisper", "groq": "Groq", "llama": "Llama"},
        )

        result = processor.process("using whisper via groq with llama model")
        assert result == "using Whisper via Groq with Llama model"

    @pytest.mark.unit
    def test_word_boundaries(self):
        """Replacements should respect word boundaries."""
        processor = TextProcessor(enable_punctuation=False, replacements={"flow": "FLOW"})

        # Should replace "flow" but not "overflow"
        assert processor.process("data flow") == "data FLOW"
        assert processor.process("overflow") == "overflow"

    @pytest.mark.unit
    def test_combined_punctuation_and_replacements(self):
        """Both features should work together."""
        processor = TextProcessor(enable_punctuation=True, replacements={"oflow": "Oflow"})

        result = processor.process("using oflow comma it works period")
        assert result == "using Oflow, it works."


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.unit
    def test_empty_text(self):
        """Empty text should be handled gracefully."""
        processor = TextProcessor(enable_punctuation=True)
        assert processor.process("") == ""

    @pytest.mark.unit
    def test_no_replacements(self):
        """Text with no matches should pass through unchanged."""
        processor = TextProcessor(enable_punctuation=True)
        assert processor.process("normal text here") == "normal text here"
