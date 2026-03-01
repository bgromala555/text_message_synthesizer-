"""Tests for FlexPersonalityProfile._coerce_and_migrate validator.

Covers the ``model_validator(mode='before')`` on ``FlexPersonalityProfile``
that handles backward-compatible field renames, age string-to-int coercion,
default population for missing fields, extra-field tolerance, and edge cases
with empty or malformed input data.
"""

# ruff: noqa: S101

from __future__ import annotations

import pytest
from pydantic import ValidationError

from source.models import FlexPersonalityProfile, FlexTextingStyle

# ---------------------------------------------------------------------------
# Valid data passthrough
# ---------------------------------------------------------------------------


class TestValidDataPassthrough:
    """Tests that well-formed data passes through the validator unchanged."""

    def test_complete_profile_preserves_all_fields(self) -> None:
        """A fully populated dict retains every value after construction."""
        data = {
            "actor_id": "A01",
            "name": "Dana",
            "age": 28,
            "cultural_background": "American",
            "neighborhood": "Brooklyn",
            "role": "friend",
            "job_details": "Software engineer",
            "personality_summary": "Friendly and outgoing",
            "emotional_range": "Expressive",
            "backstory_details": "Grew up in NYC",
            "hobbies_and_interests": ["hiking", "cooking"],
            "favorite_media": ["Breaking Bad"],
            "food_and_drink": "loves tacos",
            "favorite_local_spots": ["Central Park"],
            "current_life_situations": ["job hunting"],
            "topics_they_bring_up": ["tech"],
            "topics_they_avoid": ["politics"],
            "pet_peeves": ["lateness"],
            "humor_style": "dry wit",
            "daily_routine_notes": "early riser",
            "how_owner_talks_to_them": "casual",
            "relationship_arc": "growing closer",
            "sample_phrases": ["hey what's up"],
        }
        profile = FlexPersonalityProfile(**data)

        assert profile.actor_id == "A01"
        assert profile.name == "Dana"
        assert profile.age == 28
        assert profile.neighborhood == "Brooklyn"
        assert profile.hobbies_and_interests == ["hiking", "cooking"]
        assert profile.favorite_local_spots == ["Central Park"]
        assert profile.how_owner_talks_to_them == "casual"

    def test_integer_age_passes_through_unchanged(self) -> None:
        """An integer age value is not modified by the validator."""
        profile = FlexPersonalityProfile(name="Eli", age=45)

        assert profile.age == 45


# ---------------------------------------------------------------------------
# Age coercion
# ---------------------------------------------------------------------------


class TestAgeCoercion:
    """Tests for string-to-int age coercion in the validator."""

    @pytest.mark.parametrize(
        ("raw_age", "expected"),
        [
            ("25", 25),
            ("0", 0),
            ("100", 100),
            ("  42  ", 42),
        ],
        ids=["simple", "zero", "triple-digit", "whitespace-padded"],
    )
    def test_numeric_string_age_coerced_to_int(self, raw_age: str, expected: int) -> None:
        """Numeric string ages are converted to their integer equivalents."""
        profile = FlexPersonalityProfile(age=raw_age)  # type: ignore[arg-type]
        assert profile.age == expected

    @pytest.mark.parametrize(
        "bad_age",
        ["twenty", "", "N/A", "unknown", "12.5"],
        ids=["word", "empty", "na", "unknown", "float-string"],
    )
    def test_non_numeric_string_age_defaults_to_30(self, bad_age: str) -> None:
        """Non-numeric age strings fall back to the default value of 30."""
        profile = FlexPersonalityProfile(age=bad_age)  # type: ignore[arg-type]
        assert profile.age == 30


# ---------------------------------------------------------------------------
# Legacy field migration
# ---------------------------------------------------------------------------


class TestLegacyFieldMigration:
    """Tests for backward-compatible field renames."""

    def test_specific_nyc_haunts_migrated_to_favorite_local_spots(self) -> None:
        """The old ``specific_nyc_haunts`` key is renamed to ``favorite_local_spots``."""
        profile = FlexPersonalityProfile(specific_nyc_haunts=["Joe's Pizza", "Prospect Park"])

        assert profile.favorite_local_spots == ["Joe's Pizza", "Prospect Park"]

    def test_how_alex_talks_to_them_migrated_to_how_owner_talks_to_them(self) -> None:
        """The old ``how_alex_talks_to_them`` key is renamed to ``how_owner_talks_to_them``."""
        profile = FlexPersonalityProfile(how_alex_talks_to_them="very casual, lots of slang")

        assert profile.how_owner_talks_to_them == "very casual, lots of slang"

    def test_legacy_nyc_haunts_does_not_overwrite_existing_favorite_local_spots(self) -> None:
        """When both old and new keys are present, the new key takes priority."""
        profile = FlexPersonalityProfile(specific_nyc_haunts=["old spot"], favorite_local_spots=["new spot"])

        assert profile.favorite_local_spots == ["new spot"]

    def test_legacy_alex_does_not_overwrite_existing_how_owner_talks(self) -> None:
        """When both old and new keys are present, the new key takes priority."""
        profile = FlexPersonalityProfile(how_alex_talks_to_them="old style", how_owner_talks_to_them="new style")

        assert profile.how_owner_talks_to_them == "new style"

    def test_both_legacy_fields_migrated_simultaneously(self) -> None:
        """Both legacy renames can happen in the same construction call."""
        profile = FlexPersonalityProfile(specific_nyc_haunts=["The Met"], how_alex_talks_to_them="friendly")

        assert profile.favorite_local_spots == ["The Met"]
        assert profile.how_owner_talks_to_them == "friendly"


# ---------------------------------------------------------------------------
# Missing fields get defaults
# ---------------------------------------------------------------------------


class TestMissingFieldDefaults:
    """Tests that omitted fields receive correct default values."""

    def test_empty_construction_populates_all_defaults(self) -> None:
        """Constructing with no arguments yields a valid profile with all defaults."""
        profile = FlexPersonalityProfile()

        assert not profile.actor_id
        assert not profile.name
        assert profile.age == 30
        assert not profile.cultural_background
        assert profile.hobbies_and_interests == []
        assert profile.favorite_media == []
        assert profile.favorite_local_spots == []
        assert profile.current_life_situations == []
        assert profile.topics_they_bring_up == []
        assert profile.topics_they_avoid == []
        assert profile.pet_peeves == []
        assert profile.sample_phrases == []
        assert not profile.humor_style
        assert isinstance(profile.texting_style, FlexTextingStyle)

    def test_partial_data_fills_remaining_with_defaults(self) -> None:
        """Providing only a few fields still yields correct defaults for the rest."""
        profile = FlexPersonalityProfile(name="Zara", role="coworker")

        assert profile.name == "Zara"
        assert profile.role == "coworker"
        assert profile.age == 30
        assert not profile.neighborhood
        assert profile.hobbies_and_interests == []

    def test_texting_style_default_has_empty_fields(self) -> None:
        """The default FlexTextingStyle has empty strings for all fields."""
        profile = FlexPersonalityProfile()
        ts = profile.texting_style

        assert not ts.punctuation
        assert not ts.capitalization
        assert not ts.emoji_use
        assert not ts.abbreviations
        assert not ts.avg_message_length
        assert not ts.quirks


# ---------------------------------------------------------------------------
# Extra fields tolerance
# ---------------------------------------------------------------------------


class TestExtraFieldsTolerance:
    """Tests that unknown/extra keys are accepted without error."""

    def test_extra_keys_stored_without_error(self) -> None:
        """Extra keys from LLM output are silently accepted."""
        profile = FlexPersonalityProfile(
            name="Kai",
            favorite_color="blue",
            zodiac_sign="Leo",  # type: ignore[arg-type]
        )

        assert profile.name == "Kai"
        # Extra fields are accessible via model_extra (Pydantic v2 with extra="allow")
        assert profile.model_extra is not None
        assert profile.model_extra["favorite_color"] == "blue"
        assert profile.model_extra["zodiac_sign"] == "Leo"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for boundary and unusual inputs."""

    def test_empty_dict_produces_valid_profile(self) -> None:
        """Passing an empty dict to model_validate succeeds with all defaults."""
        profile = FlexPersonalityProfile.model_validate({})

        assert not profile.name
        assert profile.age == 30
        assert profile.hobbies_and_interests == []

    def test_none_string_fields_rejected_by_pydantic(self) -> None:
        """None for a str-typed field raises ValidationError since str fields are not Optional."""
        with pytest.raises(ValidationError, match="name"):
            FlexPersonalityProfile(name=None)  # type: ignore[arg-type]

    def test_age_absent_from_dict_uses_default(self) -> None:
        """When 'age' key is missing from the input dict, the default of 30 is used."""
        profile = FlexPersonalityProfile.model_validate({"name": "Test"})
        assert profile.age == 30

    def test_nested_texting_style_dict_parsed_correctly(self) -> None:
        """A nested dict for texting_style is parsed into a FlexTextingStyle."""
        profile = FlexPersonalityProfile(texting_style={"punctuation": "lots of periods", "emoji_use": "heavy"})  # type: ignore[arg-type]

        assert profile.texting_style.punctuation == "lots of periods"
        assert profile.texting_style.emoji_use == "heavy"
        assert not profile.texting_style.capitalization

    def test_model_validate_from_dict_with_legacy_fields(self) -> None:
        """model_validate handles legacy field migration from raw dicts."""
        raw = {
            "name": "Omar",
            "age": "33",
            "specific_nyc_haunts": ["Harlem spot"],
            "how_alex_talks_to_them": "respectful",
        }
        profile = FlexPersonalityProfile.model_validate(raw)

        assert profile.name == "Omar"
        assert profile.age == 33
        assert profile.favorite_local_spots == ["Harlem spot"]
        assert profile.how_owner_talks_to_them == "respectful"

    def test_list_fields_accept_empty_lists(self) -> None:
        """Explicitly passing empty lists is fine and preserves them."""
        profile = FlexPersonalityProfile(
            hobbies_and_interests=[],
            sample_phrases=[],
        )

        assert profile.hobbies_and_interests == []
        assert profile.sample_phrases == []
