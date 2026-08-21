import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_dir = os.path.join(self.tmpdir, "vllm")
        os.makedirs(os.path.join(self.repo_dir, "analysis"))

    def _write_analysis(self, date, commits):
        path = os.path.join(self.repo_dir, "analysis", f"{date}.json")
        with open(path, "w") as f:
            json.dump({"date": date, "repo": "vllm-project/vllm", "commits": commits}, f)

    def test_build_index_architecture_impact_is_dict(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {
                "sha": "a" * 40,
                "message": "test commit",
                "tags": ["feature", "attention"],
                "architecture_impact": {
                    "affects_architecture": True,
                    "affected_interfaces": ["AttentionBackend", "MLAAttentionSpec"],
                },
            }
        ])

        result = build_index(self.tmpdir, "vllm")
        self.assertTrue(result)

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        arch = index.get("architecture_impact_index", {})
        self.assertIn("a" * 40, arch)
        self.assertEqual(
            arch["a" * 40]["affected_interfaces"],
            ["AttentionBackend", "MLAAttentionSpec"],
        )

    def test_build_index_empty_commits(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [])
        result = build_index(self.tmpdir, "vllm")
        self.assertTrue(result)

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        self.assertEqual(index["architecture_impact_index"], {})

    def test_build_index_tags_index(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {"sha": "a" * 40, "message": "feat: add attention", "tags": ["feature", "attention", "high-risk"]},
            {"sha": "b" * 40, "message": "fix: scheduler bug", "tags": ["bugfix", "scheduler"]},
        ])

        build_index(self.tmpdir, "vllm")

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        tags = index.get("tags_index", {})
        self.assertIn("feature", tags)
        self.assertIn("bugfix", tags)
        self.assertIn("attention", tags)
        self.assertIn("scheduler", tags)

        modules = index.get("modules_index", {})
        self.assertIn("attention", modules)
        self.assertIn("scheduler", modules)
        self.assertNotIn("feature", modules)
        self.assertNotIn("high-risk", modules)

    def test_commits_index(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {"sha": "a" * 40, "message": "first commit", "tags": ["feature"]},
        ])

        build_index(self.tmpdir, "vllm")

        ci_path = os.path.join(self.repo_dir, "commits-index.json")
        with open(ci_path) as f:
            ci = json.load(f)

        self.assertIn("a" * 40, ci)
        self.assertEqual(ci["a" * 40]["date"], "2026-07-01")
        self.assertEqual(ci["a" * 40]["msg"], "first commit")


class TestExtractJson(unittest.TestCase):
    def test_extract_json_from_output_plain(self):
        from data.analyze_commits import extract_json_from_output

        result = extract_json_from_output('{"commits": [], "key": "value"}')
        self.assertEqual(result, {"commits": [], "key": "value"})

    def test_extract_json_from_output_with_code_fence(self):
        from data.analyze_commits import extract_json_from_output

        result = extract_json_from_output('```json\n{"commits": [], "key": "value"}\n```')
        self.assertEqual(result, {"commits": [], "key": "value"})

    def test_extract_json_from_output_with_trailing_text(self):
        from data.analyze_commits import extract_json_from_output

        text = '{"commits": [{"sha": "abc"}]}\n— some stats —'
        result = extract_json_from_output(text)
        self.assertEqual(result, {"commits": [{"sha": "abc"}]})

    def test_extract_json_from_output_none(self):
        from data.analyze_commits import extract_json_from_output

        self.assertIsNone(extract_json_from_output(None))
        self.assertIsNone(extract_json_from_output(""))
        self.assertIsNone(extract_json_from_output("no json here"))
        self.assertIsNone(extract_json_from_output('{"no_commits": true}'))


class TestSourceRepo(unittest.TestCase):
    def test_repo_dir_name(self):
        from data._source_repo import repo_dir_name

        self.assertEqual(repo_dir_name("vllm-project/vllm"), "vllm")
        self.assertEqual(repo_dir_name("vllm-project/vllm-ascend"), "vllm-ascend")
        self.assertEqual(repo_dir_name("custom/repo"), "repo")

    def test_known_repos_structure(self):
        from data._source_repo import KNOWN_REPOS

        for repo, config in KNOWN_REPOS.items():
            self.assertIn("dir_name", config)
            self.assertIn("url", config)
            self.assertIn("common_paths", config)
            self.assertTrue(config["url"].startswith("https://github.com/"))


class TestCleanStaleData(unittest.TestCase):
    def test_clean_stale_no_analysis_dir(self):
        from data.clean_stale_data import clean_stale_data

        tmpdir = tempfile.mkdtemp()
        repo_dir = os.path.join(tmpdir, "vllm")
        os.makedirs(os.path.join(repo_dir, "commits"))
        # No analysis dir

        result = clean_stale_data(tmpdir, "vllm-project/vllm")
        self.assertEqual(result, 0)


class TestRepoDirName(unittest.TestCase):
    def test_mcp_repo_dir_name(self):
        from mcp_server_app import repo_dir_name

        self.assertEqual(repo_dir_name("vllm"), "vllm")
        self.assertEqual(repo_dir_name("vllm-ascend"), "vllm-ascend")
        self.assertEqual(repo_dir_name("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()