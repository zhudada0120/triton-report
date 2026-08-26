"""Tests for deterministic .patch diffstat parsing (_extract_patches.py)."""
import unittest

from data._extract_patches import parse_patch_diffstat


class TestParsePatchDiffstat(unittest.TestCase):

    SAMPLE = """diff --git a/CMakeLists.txt b/CMakeLists.txt
--- a/CMakeLists.txt
+++ b/CMakeLists.txt
@@ -1,2 +1,3 @@
-line1
+line1
+line2
diff --git a/python/triton/runtime/jit.py b/python/triton/runtime/jit.py
--- a/python/triton/runtime/jit.py
+++ b/python/triton/runtime/jit.py
@@ -10 +10 @@
-old = lambda: None
+new = lambda: None
"""

    def test_extracts_files_and_counts(self):
        target_files, total_added, total_removed = parse_patch_diffstat(self.SAMPLE)

        self.assertEqual([f["path"] for f in target_files],
                         ["CMakeLists.txt", "python/triton/runtime/jit.py"])
        self.assertEqual(target_files[0]["added"], 2)
        self.assertEqual(target_files[0]["removed"], 1)
        self.assertEqual(target_files[1]["added"], 1)
        self.assertEqual(target_files[1]["removed"], 1)
        self.assertEqual(total_added, 3)
        self.assertEqual(total_removed, 2)

    def test_skips_file_header_lines(self):
        # The --- / +++ header lines must not count as removed/added content.
        content = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-old
+new
"""
        target_files, _, _ = parse_patch_diffstat(content)
        self.assertEqual(target_files[0]["added"], 1)
        self.assertEqual(target_files[0]["removed"], 1)

    def test_empty_input(self):
        target_files, total_added, total_removed = parse_patch_diffstat("")
        self.assertEqual(target_files, [])
        self.assertEqual(total_added, 0)
        self.assertEqual(total_removed, 0)


if __name__ == "__main__":
    unittest.main()
