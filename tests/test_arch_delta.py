import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestArchDeltas(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_dir = os.path.join(self.tmpdir, "vllm")
        os.makedirs(self.repo_dir)

    def test_init_and_load(self):
        from data._track_arch_delta import init_deltas, load_deltas

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        data = load_deltas(self.tmpdir, "vllm")
        self.assertIsNotNone(data)
        self.assertEqual(data["baseline_sha"], "abc123")
        self.assertEqual(data["deltas"], {})

    def test_add_delta(self):
        from data._track_arch_delta import init_deltas, add_delta, get_delta

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        add_delta(self.tmpdir, "vllm", "def456", {
            "affected_modules": ["attention/backends"],
            "affected_interfaces": ["AttentionBackend"],
            "change_summary": "add supports_non_causal",
            "ascend_impact": True,
        })

        delta = get_delta(self.tmpdir, "vllm", "def456")
        self.assertIsNotNone(delta)
        self.assertEqual(delta["affected_modules"], ["attention/backends"])
        self.assertTrue(delta["ascend_impact"])

    def test_get_deltas_since_baseline(self):
        from data._track_arch_delta import init_deltas, add_delta, get_deltas_since_baseline

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        add_delta(self.tmpdir, "vllm", "def456", {"change_summary": "c1", "ascend_impact": False})
        add_delta(self.tmpdir, "vllm", "ghi789", {"change_summary": "c2", "ascend_impact": True})

        baseline, deltas = get_deltas_since_baseline(self.tmpdir, "vllm")
        self.assertEqual(baseline, "abc123")
        self.assertEqual(len(deltas), 2)

    def test_get_deltas_up_to(self):
        from data._track_arch_delta import init_deltas, add_delta, get_deltas_up_to

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        add_delta(self.tmpdir, "vllm", "def456", {"change_summary": "c1", "ascend_impact": False})
        add_delta(self.tmpdir, "vllm", "ghi789", {"change_summary": "c2", "ascend_impact": True})

        baseline, deltas = get_deltas_up_to(self.tmpdir, "vllm", "def456")
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0][0], "def456")

    def test_reset_deltas(self):
        from data._track_arch_delta import init_deltas, add_delta, reset_deltas, delta_count

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        add_delta(self.tmpdir, "vllm", "def456", {"change_summary": "c1"})
        self.assertEqual(delta_count(self.tmpdir, "vllm"), 1)

        reset_deltas(self.tmpdir, "vllm", "new123", "2026-08-02T00:00:00")
        self.assertEqual(delta_count(self.tmpdir, "vllm"), 0)

    def test_get_affected_commits(self):
        from data._track_arch_delta import init_deltas, add_delta, get_affected_commits

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        add_delta(self.tmpdir, "vllm", "def456", {"ascend_impact": False})
        add_delta(self.tmpdir, "vllm", "ghi789", {"ascend_impact": True})

        affected = get_affected_commits(self.tmpdir, "vllm")
        self.assertEqual(affected, ["ghi789"])

    def test_delta_count(self):
        from data._track_arch_delta import init_deltas, delta_count

        init_deltas(self.tmpdir, "vllm", "abc123", "2026-08-01T00:00:00")
        self.assertEqual(delta_count(self.tmpdir, "vllm"), 0)


if __name__ == "__main__":
    unittest.main()