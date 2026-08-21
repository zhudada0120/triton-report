#!/usr/bin/env python3
"""
Adaptation status tracking CLI for vllm-ascend main2main.

Tracks which vllm upstream commits have been adapted to vllm-ascend.

Usage:
  # Initialize: scan vllm analysis for ascend_affected commits
  python src/data/track_adaptation.py init \\
    --ascend-repo-path ~/code/vllm-ascend \\
    --data-dir data

  # Initialize with explicit start date
  python src/data/track_adaptation.py init --since 2026-07-15 \\
    --ascend-repo-path ~/code/vllm-ascend

  # Show status summary
  python src/data/track_adaptation.py status

  # List commits by status
  python src/data/track_adaptation.py list --status pending

  # List all commits
  python src/data/track_adaptation.py list
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

# Paths relative to vllm-ascend repo
BASELINE_MAIN = ".github/vllm-main-verified.commit"
BASELINE_RELEASE = ".github/vllm-release-tag.commit"


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_json_atomic(filepath, data):
    dirpath = os.path.dirname(filepath)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e


def read_baseline(ascend_repo_path, filename):
    """Read a baseline file from vllm-ascend repo."""
    filepath = os.path.join(ascend_repo_path, filename)
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found")
        return None
    with open(filepath, "r") as f:
        return f.read().strip()


def find_baseline_date(data_dir, sha, vllm_repo_path=None):
    """Find the analysis date for a given SHA by scanning analysis files, then commits data, then git log."""
    analysis_dir = os.path.join(data_dir, "vllm", "analysis")
    if os.path.isdir(analysis_dir):
        for fname in sorted(os.listdir(analysis_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            date = fname.replace(".json", "")
            analysis = load_json(os.path.join(analysis_dir, fname))
            if not analysis:
                continue
            for commit in analysis.get("commits", []):
                if commit.get("sha", "")[:12] == sha[:12]:
                    return date

    commits_dir = os.path.join(data_dir, "vllm", "commits")
    if os.path.isdir(commits_dir):
        for fname in sorted(os.listdir(commits_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            date = fname.replace(".json", "")
            data = load_json(os.path.join(commits_dir, fname))
            if not data:
                continue
            for commit in data.get("commits", []):
                if commit.get("sha", "")[:12] == sha[:12]:
                    return date

    if vllm_repo_path:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%aI", sha],
                capture_output=True, text=True, timeout=10,
                cwd=vllm_repo_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()[:10]
        except Exception:
            pass

    return None


def cmd_init(args):
    """Initialize adaptation-status.json by scanning vllm analysis data.

    Baseline 之前的 commit 标记为 adapted（已被基线覆盖），
    之后的 ascend_affected commit 标记为 pending（需要适配）。
    仅维护两种状态：pending / adapted。
    """
    if not args.ascend_repo_path:
        print("Error: --ascend-repo-path is required for init")
        sys.exit(1)

    data_dir = os.path.abspath(args.data_dir)
    ascend_repo_path = os.path.abspath(args.ascend_repo_path)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    if os.path.exists(adaptation_path) and not args.force:
        print(f"adaptation-status.json already exists at {adaptation_path}")
        print("Use --force to overwrite")
        return

    # Read baselines
    main_sha = read_baseline(ascend_repo_path, BASELINE_MAIN)
    release_tag = read_baseline(ascend_repo_path, BASELINE_RELEASE)

    if not main_sha:
        print("Error: cannot read vllm-main-verified.commit")
        sys.exit(1)

    print(f"Main baseline SHA: {main_sha[:12]}")
    print(f"Release tag: {release_tag or 'N/A'}")

    # Find baseline date
    baseline_date = find_baseline_date(data_dir, main_sha, args.local_repo)
    if not baseline_date:
        print(f"Warning: Could not find baseline date for {main_sha[:12]}")
        print("Using --since argument or all available data")
    else:
        print(f"Baseline date: {baseline_date}")

    # Determine start date
    since_date = args.since or baseline_date
    if not since_date:
        print("Error: could not determine baseline date. Use --since to specify a start date.")
        sys.exit(1)

    print(f"Scanning vllm analysis from {since_date} onwards...")

    # Scan vllm analysis for ascend_affected commits
    analysis_dir = os.path.join(data_dir, "vllm", "analysis")
    if not os.path.isdir(analysis_dir):
        print(f"Error: analysis directory not found: {analysis_dir}")
        sys.exit(1)

    # First pass: collect all ascend_affected commits (including those before baseline)
    analysis_dir = os.path.join(data_dir, "vllm", "analysis")
    all_commits = []
    for fname in sorted(os.listdir(analysis_dir)):
        if not fname.endswith(".json"):
            continue
        date = fname.replace(".json", "")

        analysis = load_json(os.path.join(analysis_dir, fname))
        if not analysis:
            continue

        for ac in analysis.get("commits", []):
            ascend_impact = ac.get("ascend_impact", {})
            if ascend_impact.get("ascend_affected"):
                # 检查 deep_analysis 是否确认了是 false positive
                da = ac.get("deep_analysis", {})
                if da.get("ascend_affected_confirmed") is False:
                    continue
                all_commits.append({
                    "sha": ac["sha"],
                    "upstream_date": date,
                    "upstream_sha": ac["sha"],
                    "message": (ac.get("message", "") or "").split("\n")[0][:120],
                    "status": "adapted",  # default: adapted (will be overridden if after baseline)
                    "tags": [t for t in ac.get("tags", [])],
                    "ascend_impact_summary": ascend_impact.get("functionality", ""),
                    "adaptation_notes": "",
                    "adapted_at": None,
                    "adapted_by": None,
                })

    # Filter to tracking start date and assign status
    commits = []
    for c in all_commits:
        if c["upstream_date"] < since_date:
            continue
        # Baseline之前的 commit 标记为 adapted，之后的标记为 pending
        if baseline_date and c["upstream_date"] < baseline_date:
            c["status"] = "adapted"
            c["adapted_at"] = datetime.now(TZ_CN).isoformat()
        else:
            c["status"] = "pending"
        commits.append(c)

    stats = {
        "total": len(commits),
        "pending": sum(1 for c in commits if c["status"] == "pending"),
        "adapted": sum(1 for c in commits if c["status"] == "adapted"),
    }

    adaptation = {
        "baseline": {
            "source": f"vllm-ascend/{BASELINE_MAIN}",
            "release_tag_source": f"vllm-ascend/{BASELINE_RELEASE}",
            "main_sha": main_sha,
            "release_tag": release_tag or "",
            "tracking_start_date": since_date,
            "baseline_date": baseline_date or "",
        },
        "commits": commits,
        "stats": stats,
    }

    save_json_atomic(adaptation_path, adaptation)
    print(f"Created adaptation-status.json with {len(commits)} commits")
    print(f"Stats: total={stats['total']}, pending={stats['pending']}, adapted={stats['adapted']}")


def cmd_backfill_messages(args):
    """Backfill empty commit messages from commits-index.json."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")
    commits_index_path = os.path.join(data_dir, "vllm", "commits-index.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found.")
        sys.exit(1)

    commits_index = load_json(commits_index_path)
    if not commits_index:
        print("Error: commits-index.json not found.")
        sys.exit(1)

    filled = 0
    for commit in adaptation.get("commits", []):
        if not commit.get("message"):
            sha = commit.get("sha", "")
            entry = commits_index.get(sha)
            if entry and entry.get("msg"):
                commit["message"] = entry["msg"]
                filled += 1

    save_json_atomic(adaptation_path, adaptation)
    print(f"Backfilled {filled} commit messages from commits-index.json")


def cmd_status(args):
    """Show adaptation status summary."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    stats = adaptation.get("stats", {})
    print(f"\n{'=' * 50}")
    print(f"vllm-ascend 适配进度")
    print(f"{'=' * 50}")
    print(f"  总计:    {stats.get('total', 0)}")
    print(f"  ✅ 已适配: {stats.get('adapted', 0)}")
    print(f"  ⏳ 待适配: {stats.get('pending', 0)}")
    print(f"{'=' * 50}")

    baseline = adaptation.get("baseline", {})
    print(f"基线 SHA: {baseline.get('main_sha', 'N/A')[:12]}")
    print(f"基线日期: {baseline.get('baseline_date', 'N/A')}")
    print(f"跟踪起始: {baseline.get('tracking_start_date', 'N/A')}")


def cmd_list(args):
    """List commits by status."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    commits = adaptation.get("commits", [])
    if args.status:
        commits = [c for c in commits if c.get("status") == args.status]

    if not commits:
        print(f"No commits with status '{args.status or 'any'}'")
        return

    print(f"\nFound {len(commits)} commits:")
    print("-" * 80)
    for c in commits:
        sha_short = c["sha"][:12]
        date = c.get("upstream_date", "????-??-??")
        message = (c.get("message", "") or "")[:60]
        status_icon = {
            "pending": "⏳",
            "adapted": "✅",
        }.get(c.get("status", ""), "?")
        print(f"  {status_icon} [{sha_short}] {date} {message}")
        if c.get("ascend_impact_summary"):
            print(f"    影响: {c['ascend_impact_summary'][:80]}")
    print("-" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Track vllm-ascend adaptation status for main2main"
    )
    # Global options (available to all subcommands)
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument("--data-dir", default="data", help="Data directory")
    global_opts.add_argument("--ascend-repo-path", default=None, help="Path to vllm-ascend repository")

    subparsers = parser.add_subparsers(dest="command", help="Sub-command")

    # init
    init_parser = subparsers.add_parser("init", parents=[global_opts], help="Initialize adaptation tracking")
    init_parser.add_argument("--since", default=None, help="Start tracking from this date (YYYY-MM-DD)")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing file")
    init_parser.add_argument("--local-repo", default=None, help="Path to local vllm repository for git log fallback")

    # status
    status_parser = subparsers.add_parser("status", parents=[global_opts], help="Show adaptation status summary")

    # list
    list_parser = subparsers.add_parser("list", parents=[global_opts], help="List commits by status")
    list_parser.add_argument("--status", default=None, choices=["pending", "adapted"], help="Filter by status")

    # backfill-messages
    bf_parser = subparsers.add_parser("backfill-messages", parents=[global_opts], help="Backfill empty commit messages from commits-index.json")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Dispatch commands
    if args.command == "init":
        cmd_init(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "backfill-messages":
        cmd_backfill_messages(args)


if __name__ == "__main__":
    main()