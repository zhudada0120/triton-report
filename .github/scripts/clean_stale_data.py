#!/usr/bin/env python3
"""
Remove commit data files for dates that have no corresponding analysis.
Run this after fixing the fetch_commits bug to clean up stale data.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from data._source_repo import repo_dir_name


def clean_stale_data(data_dir, repo):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    commits_dir = os.path.join(repo_dir, "commits")
    analysis_dir = os.path.join(repo_dir, "analysis")

    if not os.path.isdir(commits_dir):
        print(f"No commits directory for {repo}")
        return

    # Get all dates that have analysis files
    analyzed_dates = set()
    if os.path.isdir(analysis_dir):
        for f in os.listdir(analysis_dir):
            if f.endswith(".json") and f != ".gitkeep":
                d = f.replace(".json", "")
                analyzed_dates.add(d)

    # Check each commit file
    removed = 0
    for f in sorted(os.listdir(commits_dir)):
        if not f.endswith(".json") or f == ".gitkeep":
            continue
        d = f.replace(".json", "")
        if d not in analyzed_dates:
            path = os.path.join(commits_dir, f)
            # Keep empty commit files (no commits on that day) — they don't need analysis
            try:
                with open(path, "r") as fh:
                    data = json.load(fh)
                if len(data.get("commits", [])) == 0:
                    continue
            except (json.JSONDecodeError, IOError):
                pass
            os.remove(path)
            print(f"  Removed {f} (no analysis for {d})")
            removed += 1

    return removed


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Clean stale commit data without analysis")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--repo", nargs="*", default=["vllm-project/vllm", "vllm-project/vllm-ascend"])
    args = parser.parse_args()

    total = 0
    for repo in args.repo:
        total += clean_stale_data(args.data_dir, repo) or 0
    print(f"Total: {total} files removed")


if __name__ == "__main__":
    main()