"""Unit tests for hallucination detection."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import is_hallucination


class TestHallucinationDetection:
    """Tests for Whisper hallucination filtering."""

    @pytest.mark.unit
    def test_youtube_hallucinations(self):
        """YouTube-style phrases should be detected as hallucinations."""
        assert is_hallucination("thanks for watching")
        assert is_hallucination("please subscribe")
        assert is_hallucination("like and subscribe")
        assert is_hallucination("don't forget to subscribe")
        assert is_hallucination("hit the bell")
        assert is_hallucination("see you next time")

    @pytest.mark.unit
    def test_ai_response_hallucinations(self):
        """AI assistant responses should be detected."""
        assert is_hallucination("I'm sorry, I cannot help")
        assert is_hallucination("As an AI, I don't have")
        assert is_hallucination("I'm not able to do that")
        assert is_hallucination("How can I help you")
        assert is_hallucination("Feel free to ask")

    @pytest.mark.unit
    def test_ai_response_starts(self):
        """AI-style response beginnings should be detected."""
        assert is_hallucination("Sure, I can help with that")
        assert is_hallucination("Certainly! Here's how")
        assert is_hallucination("Of course! The answer is")
        assert is_hallucination("Yes, I think you should")
        assert is_hallucination("Well, I think the solution is")

    @pytest.mark.unit
    def test_punctuation_only(self):
        """Punctuation-only text should be detected."""
        assert is_hallucination(".")
        assert is_hallucination("..")
        assert is_hallucination("...")
        assert is_hallucination("!")
        assert is_hallucination("?")
        assert is_hallucination(",")

    @pytest.mark.unit
    def test_empty_text(self):
        """Empty text should be handled."""
        # Empty string is too short, but not necessarily a hallucination
        # The function returns False for empty strings (not enough to determine)
        assert not is_hallucination("")
        assert not is_hallucination(None)  # None returns False (not truthy)

    @pytest.mark.unit
    def test_valid_transcriptions(self):
        """Valid transcriptions should not be detected as hallucinations."""
        assert not is_hallucination("Hello, how are you?")
        assert not is_hallucination("Please send the report by Friday")
        # Note: "subscribe" alone is a hallucination pattern, so this will be detected
        # assert not is_hallucination("I need to subscribe to this service")
        assert not is_hallucination("Thanks for the help")
        assert not is_hallucination("This is a normal sentence")

    @pytest.mark.unit
    def test_case_insensitivity(self):
        """Detection should be case insensitive."""
        assert is_hallucination("THANKS FOR WATCHING")
        assert is_hallucination("Thanks For Watching")
        assert is_hallucination("tHaNkS fOr WaTcHiNg")

    @pytest.mark.unit
    def test_partial_matches(self):
        """Real speech that merely contains a hallucination phrase must pass.

        The filter only triggers when a single pattern makes up the bulk of the
        text (or when several distinct patterns stack up). A common word like
        "subscribe" inside a longer real utterance must not be discarded.
        """
        # Real dictation that happens to contain a pattern word — keep it.
        assert not is_hallucination("I want to subscribe to the newsletter")
        assert not is_hallucination("I need to subscribe to this service")

        # But the bare hallucination phrases should still trigger.
        assert is_hallucination("subscribe to my channel")
        assert is_hallucination("please subscribe")

    @pytest.mark.unit
    def test_prompt_leakage_filtered(self):
        """Verbatim chunks of the Whisper priming prompt should be filtered."""
        # Real leakage echoes multiple priming-prompt phrases verbatim.
        assert is_hallucination("Push the code to Git and open a PR.")
        assert is_hallucination("Check the API endpoint, run pytest, then deploy to Kubernetes.")
        assert is_hallucination("Let's refactor the async handler.")

    @pytest.mark.unit
    def test_single_prompt_phrase_is_legitimate(self):
        """A single priming-prompt phrase in legit dictation must pass through.

        Regression test: the old filter substring-matched these phrases and
        silently discarded real engineering speech, leaving the user with a
        misleading 'Check your API key' error.
        """
        assert not is_hallucination("We need to push the code to staging tonight.")
        assert not is_hallucination("Can you run pytest on the auth module?")
        assert not is_hallucination("Update the e2e script to open a PR automatically.")
        assert not is_hallucination("The async handler in the worker is leaking memory.")
        assert not is_hallucination("Let's refactor the storage layer next sprint.")

    @pytest.mark.unit
    def test_loud_short_phrase_is_trusted(self):
        """A stock phrase spoken with strong mic signal is real speech, not a
        silence hallucination, and must pass through.

        Regression test: Whisper emits "Thank you." on near-silence, but the
        same words said loudly are legitimate dictation. The raw mic peak tells
        them apart — above HALLUCINATION_TRUST_PEAK we trust the transcription.
        """
        assert not is_hallucination("Thank you.", peak=0.25)
        assert not is_hallucination("thanks for watching", peak=0.30)
        assert not is_hallucination("please subscribe", peak=0.5)

    @pytest.mark.unit
    def test_quiet_short_phrase_still_filtered(self):
        """Near-silent stock phrases (and the no-peak default) stay filtered."""
        assert is_hallucination("Thank you.", peak=0.03)  # near-silence
        assert is_hallucination("Thank you.")             # unknown peak → filter
        assert is_hallucination("please subscribe", peak=0.0)

    @pytest.mark.unit
    def test_loudness_does_not_bypass_other_filters(self):
        """A loud clip still can't smuggle punctuation-only, prompt-leakage, or
        AI-response text through — only stock hallucination phrases are gated
        on signal strength."""
        assert is_hallucination(".", peak=0.9)
        assert is_hallucination("Push the code to Git and open a PR.", peak=0.9)
        assert is_hallucination("As an AI, I don't have", peak=0.9)
