"""Tests for impact_judgment_rules consumer helpers (object form, no parity logic)."""
import unittest

from data.analyze_commits import _extract_definitely_affected_paths, _format_rule_items


class TestImpactRuleConsumers(unittest.TestCase):

    def test_extracts_paths_from_objects(self):
        rules = {
            "definitely_affected_paths": [
                {"path": "python/triton/backends/compiler.py", "reason": "BaseBackend 签名"},
                {"path": "include/triton/Dialect/Triton/IR/", "reason": "IR op 定义"},
            ]
        }
        paths = _extract_definitely_affected_paths(rules)
        self.assertEqual(paths, {
            "python/triton/backends/compiler.py",
            "include/triton/Dialect/Triton/IR/",
        })

    def test_ignores_malformed_entries(self):
        rules = {
            "definitely_affected_paths": [
                {"path": "python/triton/runtime/jit.py"},
                "python/triton/language/semantic.py",   # legacy flat string — ignored
                {"reason": "no path key"},
            ]
        }
        self.assertEqual(_extract_definitely_affected_paths(rules),
                         {"python/triton/runtime/jit.py"})

    def test_strips_trailing_punctuation(self):
        # rstrip removes trailing Chinese punctuation only; the trailing slash
        # stays and is handled by the prefix-matching logic downstream.
        rules = {"definitely_affected_paths": [{"path": "python/triton/compiler/，"}]}
        self.assertEqual(_extract_definitely_affected_paths(rules),
                         {"python/triton/compiler/"})

    def test_format_rule_items_for_prompt(self):
        items = [
            {"path": "python/triton/backends/compiler.py", "reason": "接口签名"},
            {"path": "third_party/nvidia/"},
            "flat string — ignored",
        ]
        self.assertEqual(_format_rule_items(items), [
            "python/triton/backends/compiler.py（接口签名）",
            "third_party/nvidia/",
        ])


if __name__ == "__main__":
    unittest.main()
