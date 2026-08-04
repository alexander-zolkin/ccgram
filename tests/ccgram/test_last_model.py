"""Tests for the last-selected-model persistence + quick-start seeding.

CCGRAM-HOTFIX:model-picker — a fresh quick-start prompt should default to the
model Alexander last explicitly picked (per provider), not the CLI default.
"""

from __future__ import annotations

import pytest

from ccgram import last_model
from ccgram.handlers.topics.directory_browser import seed_remembered_model
from ccgram.handlers.user_state import PENDING_MODEL_ID, PENDING_MODEL_NAME


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """Point the persistence file at a throwaway CCGRAM_DIR."""
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    return tmp_path


class TestRememberRecall:
    def test_recall_empty_is_none(self, store_dir):
        assert last_model.recall_model("claude") is None

    def test_round_trip(self, store_dir):
        last_model.remember_model("claude", "claude-opus-5", "Claude Opus 5")
        assert last_model.recall_model("claude") == ("claude-opus-5", "Claude Opus 5")

    def test_per_provider_isolation(self, store_dir):
        last_model.remember_model("claude", "claude-opus-5", "Claude Opus 5")
        last_model.remember_model("grok", "grok-4.5", "Grok 4.5")
        assert last_model.recall_model("claude") == ("claude-opus-5", "Claude Opus 5")
        assert last_model.recall_model("grok") == ("grok-4.5", "Grok 4.5")

    def test_latest_pick_wins(self, store_dir):
        last_model.remember_model("claude", "claude-opus-4-8", "Claude Opus 4.8")
        last_model.remember_model("claude", "claude-opus-5", "Claude Opus 5")
        assert last_model.recall_model("claude") == ("claude-opus-5", "Claude Opus 5")

    def test_missing_name_falls_back_to_id(self, store_dir):
        last_model.remember_model("claude", "claude-opus-5", "")
        assert last_model.recall_model("claude") == ("claude-opus-5", "claude-opus-5")

    def test_blank_inputs_ignored(self, store_dir):
        last_model.remember_model("claude", "", "nope")
        assert last_model.recall_model("claude") is None

    def test_corrupt_file_is_none_never_raises(self, store_dir):
        (store_dir / "last_model.json").write_text("{not json")
        assert last_model.recall_model("claude") is None  # no exception


class TestSeedRememberedModel:
    def test_seeds_when_absent(self, store_dir):
        last_model.remember_model("claude", "claude-opus-5", "Claude Opus 5")
        user_data: dict = {}
        seed_remembered_model(user_data, "claude")
        assert user_data[PENDING_MODEL_ID] == "claude-opus-5"
        assert user_data[PENDING_MODEL_NAME] == "Claude Opus 5"

    def test_noop_when_pending_already_set(self, store_dir):
        last_model.remember_model("claude", "claude-opus-5", "Claude Opus 5")
        user_data = {PENDING_MODEL_ID: "claude-sonnet-5", PENDING_MODEL_NAME: "S5"}
        seed_remembered_model(user_data, "claude")
        assert user_data[PENDING_MODEL_ID] == "claude-sonnet-5"  # in-flight pick wins

    def test_noop_when_nothing_remembered(self, store_dir):
        user_data: dict = {}
        seed_remembered_model(user_data, "claude")
        assert PENDING_MODEL_ID not in user_data

    def test_none_user_data_is_safe(self, store_dir):
        seed_remembered_model(None, "claude")  # must not raise
