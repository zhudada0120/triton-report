#!/usr/bin/env python3
"""
Adaptation status tracking CLI for triton → triton-ascend manual cherry-pick sync.

triton-ascend has NO baseline files (no main2main sync). Upstream triton
commits are manually cherry-picked / re-applied by maintainers. Adapted
detection therefore uses:

  1. History scan: upstream SHA exists verbatim in triton-ascend git history
     (covers the early merge-based period; cat-file --batch-check).
  2. Cherry-pick markers: "(cherry picked from commit <sha>)" lines found in
     triton-ascend commit messages.
  3. Manual marking: `mark` subcommand for the rest (manual re-application
     without markers is undetectable from git alone).

Usage:
  # Initialize: scan triton analysis for ascend_affected commits, detect adapted
  python src/data/track_adaptation.py init \\
    --ascend-repo-path ~/code/triton-ascend \\
    --data-dir data

  # Initialize with explicit start date
  python src/data/track_adaptation.py init --since 2026-07-15 \\
    --ascend-repo-path ~/code/triton-ascend

  # Refresh adapted detection (daily): pending → adapted when now detectable
  python src/data/track_adaptation.py detect \\
    --ascend-repo-path ~/code/triton-ascend

  # Manually mark a commit (manual cherry-pick without markers)
  python src/data/track_adaptation.py mark --sha <sha12> --status adapted \\
    --note "ported in PR #1234"

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
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

# Regex for "(cherry picked from commit <sha>)" markers in commit messages
CHERRY_PICK_RE = re.compile(r"cherry[- ]picked from commit\s+([0-9a-fA-F]{7,40})")


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


def detect_adapted_shas(ascend_repo_path, candidate_shas):
    """Detect which upstream SHAs are adapted into triton-ascend.

    Two sources:
      1. SHA exists verbatim in ascend git history (merge-based period)
      2. cherry-pick marker in ascend commit messages
    Returns a set of full-length SHAs considered adapted.
    """
    adapted = set()
    if not candidate_shas:
        return adapted

    # ── 1. History scan: batch-check SHA existence ────────────────────
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch-check"],
            input="\n".join(candidate_shas) + "\n",
            capture_output=True, text=True, timeout=60,
            cwd=ascend_repo_path,
        )
        for line in proc.stdout.strip().split("\n"):
            if not line.strip():
                continue
            sha = line.split()[0]
            if sha in candidate_shas and " missing" not in line:
                adapted.add(sha)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Warning: history scan failed: {e}")

    # ── 2. Cherry-pick marker scan ────────────────────────────────────
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--format=%B", "--grep=cherry picked from commit", "-i"],
            capture_output=True, text=True, timeout=120,
            cwd=ascend_repo_path,
        )
        for sha in CHERRY_PICK_RE.findall(result.stdout):
            # Match by prefix against candidate full SHAs
            for full in candidate_shas:
                if full.startswith(sha.lower()) and full not in adapted:
                    adapted.add(full)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Warning: cherry-pick marker scan failed: {e}")

    return adapted


def collect_ascend_affected(data_dir, since_date=None):
    """Scan triton analysis data for ascend_affected commits.

    Returns a list of commit dicts (same shape as the vllm original so the
    adaptation-status.json format stays compatible).
    """
    analysis_dir = os.path.join(data_dir, "triton", "analysis")
    if not os.path.isdir(analysis_dir):
        return []

    commits = []
    for fname in sorted(os.listdir(analysis_dir)):
        if not fname.endswith(".json"):
            continue
        date = fname.replace(".json", "")
        if since_date and date < since_date:
            continue

        analysis = load_json(os.path.join(analysis_dir, fname))
        if not analysis:
            continue

        for ac in analysis.get("commits", []):
            ascend_impact = ac.get("ascend_impact", {})
            if not ascend_impact.get("ascend_affected"):
                continue
            # Skip Phase-2-confirmed false positives
            da = ac.get("deep_analysis", {})
            if da.get("ascend_affected_confirmed") is False:
                continue
            commits.append({
                "sha": ac["sha"],
                "upstream_date": date,
                "upstream_sha": ac["sha"],
                "message": (ac.get("message", "") or "").split("\n")[0][:120],
                "status": "pending",  # default; detection pass overrides
                "tags": [t for t in ac.get("tags", [])],
                "ascend_impact_summary": ascend_impact.get("functionality", ""),
                "adaptation_notes": "",
                "adapted_at": None,
                "adapted_by": None,
            })
    return commits


def cmd_init(args):
    """Initialize adaptation-status.json by scanning triton analysis data.

    Every ascend_affected commit starts as `pending`, then the git-history
    detection pass marks those already adapted in triton-ascend as `adapted`.
    """
    if not args.ascend_repo_path:
        print("Error: --ascend-repo-path is required for init")
        sys.exit(1)

    data_dir = os.path.abspath(args.data_dir)
    ascend_repo_path = os.path.abspath(args.ascend_repo_path)
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")

    if os.path.exists(adaptation_path) and not args.force:
        print(f"adaptation-status.json already exists at {adaptation_path}")
        print("Use --force to overwrite")
        return

    since_date = args.since
    print(f"Scanning triton analysis{' from ' + since_date if since_date else ''}...")

    commits = collect_ascend_affected(data_dir, since_date)
    if not commits:
        print("No ascend_affected commits found in analysis data.")
        print("Run analyze_commits.py first (needs data/triton/analysis/*.json).")
        sys.exit(1)

    # Detect adapted SHAs from ascend git history
    candidate_shas = [c["sha"] for c in commits]
    adapted_shas = detect_adapted_shas(ascend_repo_path, candidate_shas)

    for c in commits:
        if c["sha"] in adapted_shas:
            c["status"] = "adapted"
            c["adapted_at"] = datetime.now(TZ_CN).isoformat()
            c["adapted_by"] = "history-scan"

    stats = {
        "total": len(commits),
        "pending": sum(1 for c in commits if c["status"] == "pending"),
        "adapted": sum(1 for c in commits if c["status"] == "adapted"),
    }

    ascend_sha = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=ascend_repo_path,
        )
        ascend_sha = result.stdout.strip()
    except Exception:
        pass

    adaptation = {
        "baseline": {
            "mode": "history-scan",
            "detection": "upstream SHA in ascend git history OR cherry-pick marker",
            "ascend_repo_sha": ascend_sha or "",
            "tracking_start_date": since_date or "",
            "detected_at": datetime.now(TZ_CN).isoformat(),
            "note": "triton-ascend 无 baseline 文件（人工 cherry-pick 回合）。"
                    "无标记的人工回合请用 `mark` 命令手动更新。",
        },
        "commits": commits,
        "stats": stats,
    }

    save_json_atomic(adaptation_path, adaptation)
    print(f"Created adaptation-status.json with {len(commits)} commits")
    print(f"Stats: total={stats['total']}, pending={stats['pending']}, adapted={stats['adapted']} (history-scan)")


def cmd_detect(args):
    """Refresh adapted detection: mark newly-detectable pending commits as adapted.

    Only promotes pending → adapted, never demotes (manual marks are preserved).
    """
    if not args.ascend_repo_path:
        print("Error: --ascend-repo-path is required for detect")
        sys.exit(1)

    data_dir = os.path.abspath(args.data_dir)
    ascend_repo_path = os.path.abspath(args.ascend_repo_path)
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    commits = adaptation.get("commits", [])
    pending_shas = [c["sha"] for c in commits if c.get("status") == "pending"]
    if not pending_shas:
        print("No pending commits to check.")
        return

    print(f"Checking {len(pending_shas)} pending commits against ascend git history...")
    adapted_shas = detect_adapted_shas(ascend_repo_path, pending_shas)

    promoted = 0
    for c in commits:
        if c.get("status") == "pending" and c["sha"] in adapted_shas:
            c["status"] = "adapted"
            c["adapted_at"] = datetime.now(TZ_CN).isoformat()
            c["adapted_by"] = "history-scan"
            promoted += 1

    stats = {
        "total": len(commits),
        "pending": sum(1 for c in commits if c["status"] == "pending"),
        "adapted": sum(1 for c in commits if c["status"] == "adapted"),
    }
    adaptation["stats"] = stats
    if adaptation.get("baseline"):
        adaptation["baseline"]["detected_at"] = datetime.now(TZ_CN).isoformat()

    save_json_atomic(adaptation_path, adaptation)
    print(f"Promoted {promoted} commits to adapted.")
    print(f"Stats: total={stats['total']}, pending={stats['pending']}, adapted={stats['adapted']}")


def cmd_mark(args):
    """Manually set a commit's adaptation status (with optional note)."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    sha_prefix = args.sha.lower()
    matched = [c for c in adaptation.get("commits", []) if c["sha"].startswith(sha_prefix)]
    if not matched:
        print(f"Error: no commit matching sha prefix {sha_prefix}")
        sys.exit(1)
    if len(matched) > 1:
        print(f"Error: sha prefix {sha_prefix} matches {len(matched)} commits, use more digits")
        sys.exit(1)

    commit = matched[0]
    commit["status"] = args.status
    if args.status == "adapted":
        commit["adapted_at"] = datetime.now(TZ_CN).isoformat()
        commit["adapted_by"] = args.by or "manual"
    else:
        commit["adapted_at"] = None
        commit["adapted_by"] = None
    if args.note:
        commit["adaptation_notes"] = args.note

    stats = {
        "total": len(adaptation["commits"]),
        "pending": sum(1 for c in adaptation["commits"] if c["status"] == "pending"),
        "adapted": sum(1 for c in adaptation["commits"] if c["status"] == "adapted"),
    }
    adaptation["stats"] = stats

    save_json_atomic(adaptation_path, adaptation)
    print(f"Marked {commit['sha'][:12]} as {args.status}")
    print(f"Stats: total={stats['total']}, pending={stats['pending']}, adapted={stats['adapted']}")


def cmd_backfill_messages(args):
    """Backfill empty commit messages from commits-index.json."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")
    commits_index_path = os.path.join(data_dir, "triton", "commits-index.json")

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
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    stats = adaptation.get("stats", {})
    print(f"\n{'=' * 50}")
    print(f"triton-ascend 适配进度")
    print(f"{'=' * 50}")
    print(f"  总计:    {stats.get('total', 0)}")
    print(f"  ✅ 已适配: {stats.get('adapted', 0)}")
    print(f"  ⏳ 待适配: {stats.get('pending', 0)}")
    print(f"{'=' * 50}")

    baseline = adaptation.get("baseline", {})
    print(f"检测模式: {baseline.get('mode', 'N/A')}（{baseline.get('detection', '')}）")
    print(f"跟踪起始: {baseline.get('tracking_start_date', 'N/A')}")
    print(f"上次检测: {baseline.get('detected_at', 'N/A')[:19]}")


def cmd_list(args):
    """List commits by status."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "triton-ascend", "adaptation-status.json")

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
        description="Track triton-ascend adaptation status (manual cherry-pick sync)"
    )
    # Global options (available to all subcommands)
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument("--data-dir", default="data", help="Data directory")
    global_opts.add_argument("--ascend-repo-path", default=None, help="Path to triton-ascend repository")

    subparsers = parser.add_subparsers(dest="command", help="Sub-command")

    # init
    init_parser = subparsers.add_parser("init", parents=[global_opts], help="Initialize adaptation tracking")
    init_parser.add_argument("--since", default=None, help="Start tracking from this date (YYYY-MM-DD)")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing file")

    # detect
    detect_parser = subparsers.add_parser("detect", parents=[global_opts], help="Refresh adapted detection (pending → adapted)")

    # mark
    mark_parser = subparsers.add_parser("mark", parents=[global_opts], help="Manually set a commit's status")
    mark_parser.add_argument("--sha", required=True, help="Upstream commit SHA (or unique prefix)")
    mark_parser.add_argument("--status", required=True, choices=["pending", "adapted"], help="Target status")
    mark_parser.add_argument("--note", default=None, help="Adaptation note (e.g. ported PR link)")
    mark_parser.add_argument("--by", default=None, help="Who/what marked this (default: manual)")

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
    elif args.command == "detect":
        cmd_detect(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "backfill-messages":
        cmd_backfill_messages(args)


if __name__ == "__main__":
    main()
