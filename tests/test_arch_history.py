"""Tests for architecture_history baseline version tracking."""
import unittest

from data.generate_context import _build_architecture_history


class TestArchitectureHistory(unittest.TestCase):

    def test_first_generation_starts_history(self):
        h = _build_architecture_history([], "abc123", "2026-08-26T10:00:00+08:00")
        self.assertEqual(h, [{"commit_sha": "abc123", "generated_at": "2026-08-26T10:00:00+08:00"}])

    def test_regeneration_appends_new_baseline(self):
        prev = [{"commit_sha": "abc123", "generated_at": "2026-08-21T10:00:00+08:00"}]
        h = _build_architecture_history(prev, "def456", "2026-08-26T10:00:00+08:00")
        self.assertEqual([e["commit_sha"] for e in h], ["abc123", "def456"])

    def test_same_sha_not_duplicated(self):
        prev = [{"commit_sha": "abc123", "generated_at": "2026-08-21T10:00:00+08:00"}]
        h = _build_architecture_history(prev, "abc123", "2026-08-26T10:00:00+08:00")
        self.assertEqual(len(h), 1)

    def test_unknown_sha_not_recorded(self):
        prev = [{"commit_sha": "abc123", "generated_at": "2026-08-21T10:00:00+08:00"}]
        h = _build_architecture_history(prev, "unknown", "2026-08-26T10:00:00+08:00")
        self.assertEqual([e["commit_sha"] for e in h], ["abc123"])

    def test_non_dict_entries_filtered(self):
        prev = ["garbage", {"commit_sha": "abc123", "generated_at": "x"}]
        h = _build_architecture_history(prev, "def456", "y")
        self.assertEqual([e["commit_sha"] for e in h], ["abc123", "def456"])


if __name__ == "__main__":
    unittest.main()
