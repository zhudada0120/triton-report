#!/usr/bin/env python3
"""
Extract patch inventory from triton-ascend's third_party/ascend/patch/ directory.

triton-ascend maintains monolithic .patch files (unlike vllm-ascend's
patch/__init__.py catalog):
  - triton-ascend-<version>.patch     release patch
  - triton-ascend-dev-<version>.patch dev patch
  - llvm_patch_<sha>.patch            LLVM/MLIR patch

This script parses the .patch files deterministically (no LLM) to extract
per-file diffstat — which files each patch touches and how heavily.

Output: structured JSON suitable for arch.json's knowledge_base.patch_catalog.
"""
import argparse
import json
import os
import sys

# Path relative to the triton-ascend repo root
PATCH_DIR = os.path.join("third_party", "ascend", "patch")


def parse_patch_diffstat(content):
    """Parse a unified diff and return per-file added/removed line counts.

    Returns (target_files, total_added, total_removed).
    """
    target_files = []
    current = None
    for line in content.split("\n"):
        if line.startswith("diff --git "):
            # e.g. diff --git a/CMakeLists.txt b/CMakeLists.txt
            m = line.split(" b/", 1)
            if len(m) == 2:
                current = {"path": m[1].strip(), "added": 0, "removed": 0}
                target_files.append(current)
            continue
        if current is None:
            continue
        # Skip the ---/+++ file headers (they start with -/+ but are not content)
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"] += 1

    total_added = sum(f["added"] for f in target_files)
    total_removed = sum(f["removed"] for f in target_files)
    return target_files, total_added, total_removed


def build_patch_catalog(ascend_repo_path):
    """Build the full patch catalog from third_party/ascend/patch/*.patch."""
    patch_dir = os.path.join(ascend_repo_path, PATCH_DIR)
    if not os.path.isdir(patch_dir):
        print(f"Error: {patch_dir} not found")
        return {"patch_files": []}

    patch_files = []
    for fname in sorted(os.listdir(patch_dir)):
        if not fname.endswith(".patch"):
            continue
        # llvm_patch_*.patch patch the LLVM dependency, not triton source;
        # upstream triton commits can never touch those files.
        # triton-ascend-dev-*.patch applies only in dev builds (main-dev /
        # version.txt containing "dev", see setup_ascend.py:_is_dev_mode);
        # the pipeline analyzes main, so exclude it as well.
        if fname.startswith(("llvm_patch_", "triton-ascend-dev-")):
            continue
        filepath = os.path.join(patch_dir, fname)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        target_files, total_added, total_removed = parse_patch_diffstat(content)
        patch_files.append({
            "name": fname,
            "size_bytes": os.path.getsize(filepath),
            "total_added": total_added,
            "total_removed": total_removed,
            "target_files": target_files,
        })

    return {
        "source": PATCH_DIR,
        "patch_files": patch_files,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract patch inventory from triton-ascend third_party/ascend/patch/*.patch"
    )
    parser.add_argument(
        "--ascend-repo-path", required=True,
        help="Path to triton-ascend repository"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output JSON file (default: print to stdout)"
    )
    args = parser.parse_args()

    catalog = build_patch_catalog(args.ascend_repo_path)

    output = json.dumps(catalog, ensure_ascii=False, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Patch catalog saved to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
