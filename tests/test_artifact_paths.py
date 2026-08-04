"""Locator test: the Bazel-built trainer bundle resolves to a real file."""

import sys

import pytest

from tests.artifact_paths import trainer_js_path, trainer_min_js_path

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_trainer_js_locator_resolves():
    assert trainer_js_path().exists()


def test_trainer_min_js_locator_resolves():
    assert trainer_min_js_path().exists()
