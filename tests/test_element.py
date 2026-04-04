"""Tests for the Element dataclass."""

import sys
import pytest

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.element import Element


class TestStr:
    def test_with_emoji(self):
        assert str(Element("Water", "💧")) == "💧 Water"

    def test_without_emoji(self):
        assert str(Element("Mud")) == "Mud"

    def test_empty_emoji(self):
        assert str(Element("Mud", "")) == "Mud"

    def test_none_name(self):
        assert str(Element(name=None)) == ""


class TestEquality:
    def test_same_name(self):
        assert Element("Water", "💧") == Element("Water", "🌊")

    def test_different_name(self):
        assert Element("Water") != Element("Fire")

    def test_not_element(self):
        assert Element("Water") != "Water"


class TestHash:
    def test_same_name_same_hash(self):
        assert hash(Element("Water", "💧")) == hash(Element("Water", "🌊"))

    def test_usable_in_set(self):
        s = {Element("Water"), Element("Water", "💧"), Element("Fire")}
        assert len(s) == 2

    def test_usable_as_dict_key(self):
        d = {Element("Water"): 1}
        assert d[Element("Water", "💧")] == 1


class TestBool:
    def test_all_set(self):
        assert bool(Element("Water", "💧", False)) is True

    def test_name_none(self):
        assert bool(Element(name=None)) is False

    def test_emoji_none(self):
        assert bool(Element("Water", None, False)) is False

    def test_is_first_discovery_none(self):
        assert bool(Element("Water", "💧", None)) is False


class TestFrozen:
    def test_cannot_set_name(self):
        e = Element("Water")
        with pytest.raises(AttributeError):
            e.name = "Fire"

    def test_cannot_set_emoji(self):
        e = Element("Water", "💧")
        with pytest.raises(AttributeError):
            e.emoji = "🔥"
