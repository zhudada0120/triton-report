#!/usr/bin/env python3
"""
从本地 git 仓库获取指定日期的 commit 数据，写入 data/ 目录。

用法：
  python src/data/fetch_commits.py \
      --repo vllm-project/vllm \
      --local-repo ~/code/vllm \
      --date 2026-07-27

执行流程：
  1. pull 最新代码，确保获取到最新的 commit 列表
  2. 用 git log --after/--before 获取指定日期的 commit SHA 列表
  3. 逐个调用 git show 获取每个 commit 的完整 diff/patch
  4. 按日期分组写入 data/{repo}/commits/{date}.json
"""
import argparse
import json
import os
import sys
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data._source_repo import repo_dir_name

TZ_CN = timezone(timedelta(hours=8))


def convert_to_cn_time(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_cn = dt.astimezone(TZ_CN)
        return dt_cn.isoformat()
    except (ValueError, AttributeError):
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


def get_commit_detail(local_repo, sha):
    try:
        result = subprocess.run(
            ["git", "show", "--format=%H%n%an%n%ae%n%aI%n%B", "--patch", sha],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        output = result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    lines = output.split("\n")
    if len(lines) < 5:
        return None

    sha_out = lines[0]
    author_name = lines[1]
    author_email = lines[2]
    author_date = lines[3]

    body_start = 4
    body_end = len(lines)
    for i in range(body_start, len(lines)):
        if lines[i].startswith("diff --git"):
            body_end = i
            break
    message = "\n".join(lines[body_start:body_end]).strip()

    diff_start = None
    for i in range(body_end, len(lines)):
        if lines[i].startswith("diff --git"):
            diff_start = i
            break

    stats = {"total_additions": 0, "total_deletions": 0, "files_changed": 0}
    files = []

    if diff_start is not None:
        current_file = None
        file_additions = 0
        file_deletions = 0
        file_patch_lines = []

        i = diff_start
        while i < len(lines):
            line = lines[i]
            if line.startswith("diff --git"):
                if current_file is not None:
                    current_file["additions"] = file_additions
                    current_file["deletions"] = file_deletions
                    current_file["patch"] = "\n".join(file_patch_lines)
                    files.append(current_file)
                    stats["total_additions"] += file_additions
                    stats["total_deletions"] += file_deletions
                    stats["files_changed"] += 1

                parts = line.split(" b/", 1)
                fname = parts[1] if len(parts) == 2 else (line.split()[-1] if line.split() else "unknown")
                current_file = {"filename": fname, "status": "modified", "additions": 0, "deletions": 0, "patch": ""}
                file_additions = 0
                file_deletions = 0
                file_patch_lines = []
                i += 1
                continue

            if current_file is not None:
                if line.startswith("new file"):
                    current_file["status"] = "added"
                elif line.startswith("deleted file"):
                    current_file["status"] = "removed"
                elif line.startswith("rename from"):
                    current_file["status"] = "renamed"

                if line.startswith("+") and not line.startswith("+++"):
                    file_additions += 1
                    file_patch_lines.append(line)
                elif line.startswith("-") and not line.startswith("---"):
                    file_deletions += 1
                    file_patch_lines.append(line)
                elif line.startswith("@@"):
                    file_patch_lines.append(line)
                elif not line.startswith("index ") and not line.startswith("---") and not line.startswith("+++") and not line.startswith("Binary"):
                    file_patch_lines.append(line)

            i += 1

        if current_file is not None:
            current_file["additions"] = file_additions
            current_file["deletions"] = file_deletions
            current_file["patch"] = "\n".join(file_patch_lines)
            files.append(current_file)
            stats["total_additions"] += file_additions
            stats["total_deletions"] += file_deletions
            stats["files_changed"] += 1

    parent_shas = []
    try:
        p_result = subprocess.run(
            ["git", "log", "--format=%P", "-1", sha],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p_result.returncode == 0 and p_result.stdout.strip():
            parent_shas = p_result.stdout.strip().split()
    except Exception:
        pass

    return {
        "sha": sha_out,
        "author": {"name": author_name, "email": author_email},
        "date": author_date,
        "message": message,
        "parents": parent_shas,
        "stats": stats,
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch commits from a local git repo for a specific date")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/repo)")
    parser.add_argument("--branch", default="main", help="Branch to track")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--local-repo", required=True, help="Path to local repo source code")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (UTC+8)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file")
    args = parser.parse_args()

    local_repo = os.path.abspath(args.local_repo)
    if not os.path.isdir(local_repo):
        print(f"Error: local repo not found at {local_repo}")
        sys.exit(1)

    # Pull latest to ensure we have the most recent commits
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", args.branch],
            cwd=local_repo, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                print(f"  Repo already up to date")
            else:
                print(f"  Pulled latest: {output[:100]}")
        else:
            print(f"  Warning: pull failed: {result.stderr[:100]}")
    except Exception as e:
        print(f"  Warning: pull failed: {e}")

    output_path = os.path.abspath(os.path.join(args.data_dir, repo_dir_name(args.repo), "commits", f"{args.date}.json"))
    if os.path.exists(output_path):
        if not args.force:
            print(f"{output_path} already exists, skipping (use --force to overwrite)")
            return
        print(f"Warning: overwriting {output_path}")

    after = f"{args.date}T00:00:00+08:00"
    before = f"{args.date}T23:59:59+08:00"
    result = subprocess.run(
        ["git", "log", "--format=%H", "--after", after, "--before", before, args.branch],
        cwd=local_repo, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"git log failed: {result.stderr[:200]}")
        sys.exit(1)

    shas = [s for s in result.stdout.strip().split("\n") if s]
    print(f"Found {len(shas)} commits on {args.date}")

    commits_detail = []
    for i, sha in enumerate(shas):
        detail = get_commit_detail(local_repo, sha)
        if detail:
            detail["date"] = convert_to_cn_time(detail["date"])
            commits_detail.append(detail)

    data = {
        "date": args.date,
        "repo": args.repo,
        "branch": args.branch,
        "commits": commits_detail,
    }
    save_json_atomic(output_path, data)
    print(f"Wrote {len(commits_detail)} commits to {output_path}")


if __name__ == "__main__":
    main()