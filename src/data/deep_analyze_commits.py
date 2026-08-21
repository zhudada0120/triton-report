#!/usr/bin/env python3
"""
Phase 2: 用 opencode agent 深度分析 ascend_affected 的 commit。

用法：
  python src/data/deep_analyze_commits.py \
      --repo vllm-project/vllm \
      --date 2026-07-27 \
      --local-repo ~/code/vllm \
      --data-dir data

执行流程：
  1. 读取 Phase 1 的分析结果，找出 ascend_affected=true 且尚未深度分析的 commit
  2. 对每个 commit：
     a. 临时 checkout vllm 源码到该 commit 的父 commit（还原分析时的代码状态）
     b. 调用 opencode（agent 模式），读取架构文件和实际源码
     c. 获取深度分析结果（affected_interfaces、adaptation_effort、adaptation_guide、risk）
     d. 写回 deep_analysis 字段到分析文件（每个 commit 完成后即时保存）
     e. 恢复 vllm 源码到原来的 HEAD
  3. 全部完成后打印总结
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data._source_repo import repo_dir_name
from data._track_arch_delta import get_deltas_up_to, add_delta, load_deltas, deltas_path, save_json_atomic as save_deltas
from data._source_cache import SourceContextCache
from data._opencode_client import call_opencode

TZ_CN = timezone(timedelta(hours=8))


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return None


def save_json_atomic(filepath, data):
    import tempfile
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


def deep_analyze_commits(repo, date, data_dir, local_repo):
    total_start = time.time()
    repo_dir = get_repo_dir(data_dir, repo)
    analysis_path = os.path.join(repo_dir, "analysis", f"{date}.json")

    analysis = load_json(analysis_path)
    if analysis is None:
        print(f"No analysis found at {analysis_path}")
        return False

    to_analyze = [
        ac for ac in analysis.get("commits", [])
        if ac.get("ascend_impact", {}).get("ascend_affected")
        and "deep_analysis" not in ac
    ]

    if not to_analyze:
        print("No ascend_affected commits without deep_analysis found")
        return True

    print(f"Deep analyzing {len(to_analyze)} ascend_affected commits via opencode...")

    commits_path = os.path.join(repo_dir, "commits", f"{date}.json")
    commits_data = load_json(commits_path)
    all_commits = commits_data.get("commits", []) if commits_data else []

    repo_short = repo_dir_name(repo)
    data_dir_abs = os.path.abspath(data_dir) if not os.path.isabs(data_dir) else data_dir
    repo_dir_abs = os.path.join(data_dir_abs, repo_short)
    cache = SourceContextCache(repo_dir_abs, repo_dir=local_repo)

    total = len(to_analyze)
    for i, item in enumerate(to_analyze, 1):
        sha = item["sha"]
        sha_short = sha[:12]
        round_start = time.time()
        print(f"\n  [{i}/{total}] Deep analyzing {sha_short}...")

        original_head = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=local_repo, capture_output=True, text=True, timeout=15,
            )
            original_head = result.stdout.strip()

            parent_result = subprocess.run(
                ["git", "rev-parse", f"{sha}^"],
                cwd=local_repo, capture_output=True, text=True, timeout=15,
            )
            parent_sha = sha if parent_result.returncode != 0 else parent_result.stdout.strip()

            print(f"    Checking out vllm source at {parent_sha[:12]} (parent of {sha_short})...")
            subprocess.run(
                ["git", "checkout", "--force", parent_sha],
                cwd=local_repo, capture_output=True, text=True, timeout=60,
            )

            commit_info = None
            for c in all_commits:
                if c["sha"] == sha:
                    commit_info = c
                    break

            if commit_info is None:
                print(f"    No commit info found for {sha_short}, skipping")
                continue

            files_changed = [f["filename"] for f in commit_info.get("files", [])]
            # 构建源码缓存摘要
            full_paths = [
                os.path.join(local_repo, f)
                for f in files_changed
                if os.path.exists(os.path.join(local_repo, f))
            ]
            source_cache_text = cache.get_batch_summary(full_paths)
            patch_summary = "\n".join(
                f"  {f['filename']}: +{f['additions']}/-{f['deletions']}"
                for f in commit_info.get("files", [])
            )[:2000]

            baseline_sha, deltas = get_deltas_up_to(data_dir, repo_short, sha)
            deltas = [(s, d) for s, d in deltas if s != sha and d.get('change_summary', '').strip()]

            delta_context = ""
            if deltas:
                delta_lines = []
                for s, d in deltas:
                    delta_lines.append(
                        f"  - {s[:12]}: {d.get('change_summary', '')} "
                        f"(modules: {', '.join(d.get('affected_modules', []))})"
                    )
                delta_context = (
                    f"\n## 自基线以来的架构增量变更（{len(deltas)} 个 commit）\n"
                    + "\n".join(delta_lines)
                    + "\n注意：这些是此 commit 之前的架构变化，已反映在代码中。"
                )

            msg = (commit_info.get('message', '') or '').split('\n')[0][:120]
            prompt = f"""You are analyzing vllm commit {sha_short} that may affect vllm-ascend. Provide deep analysis.

## Repository
- vllm: {repo}
- vllm-ascend: vllm-project/vllm-ascend
- Date: {date}
- Commit: {sha_short}
- Commit message: {msg}

## Architecture Context
Read the architecture.json files for both projects at:
- {data_dir_abs}/vllm/context/architecture.json
- {data_dir_abs}/vllm-ascend/context/architecture.json

IMPORTANT: architecture.json is a **baseline snapshot**. It was generated at an earlier commit.
The actual code checked out below is at the PARENT of this commit, so it reflects the code
BEFORE this commit was applied. The architecture.json describes the general project structure,
but the source code is the ground truth for what exists at this point in time.

Focus on the interface_surface and cross_project_relationship fields to understand impact rules,
but ALWAYS verify against the actual source code checked out at {local_repo}.

The vllm source code is checked out at: {local_repo}
IMPORTANT: The source code is at the commit just BEFORE this commit ({parent_sha[:12]}).
This is the correct state for analyzing what this commit changes.
Read relevant source files to understand the code and interfaces that this commit modifies.
{delta_context}
{source_cache_text}

## Commit to Analyze
Files changed: {', '.join(files_changed)}

Patch summary:
{patch_summary}

## Analysis Requirements
1. ascend_affected_confirmed: Is this commit a TRUE positive? Set to false if it doesn't actually require any adaptation in vllm-ascend (Phase 1 false positive).
2. affected_interfaces: Which specific interfaces/classes in vllm-ascend are affected (empty array if ascend_affected_confirmed is false)
3. adaptation_effort: How much adaptation work is needed (low/medium/high). Set to "low" if ascend_affected_confirmed is false.
4. adaptation_guide: What needs to be adapted in vllm-ascend (be specific about files and methods). Empty string if ascend_affected_confirmed is false.
5. risk: Risk assessment of the adaptation. Empty string if ascend_affected_confirmed is false.
"""

            deep_analysis_schema = {
                "type": "object",
                "properties": {
                    "ascend_affected_confirmed": {
                        "type": "boolean",
                        "description": "Whether this commit truly requires adaptation in vllm-ascend. Set to false if Phase 1 was a false positive.",
                    },
                    "affected_interfaces": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "adaptation_effort": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "adaptation_guide": {
                        "type": "string",
                        "description": "What needs to be adapted in vllm-ascend",
                    },
                    "risk": {"type": "string"},
                },
                "required": ["ascend_affected_confirmed", "affected_interfaces", "adaptation_effort", "adaptation_guide"],
            }

            result = call_opencode(
                prompt=prompt,
                json_schema=deep_analysis_schema,
                add_dirs=[local_repo, data_dir_abs],
            )

            if result:
                item["deep_analysis"] = result
                round_elapsed = int(time.time() - round_start)
                print(f"  [{i}/{total}] ✓ {sha_short} 分析完成 ({round_elapsed}s)")

                if result.get("ascend_affected_confirmed") is False:
                    item.setdefault("ascend_impact", {})["ascend_affected"] = False
                    print(f"  [{i}/{total}] Phase 1 false positive: ascend_affected overridden to false")
            else:
                round_elapsed = int(time.time() - round_start)
                print(f"  [{i}/{total}] ✗ {sha_short} 分析失败 ({round_elapsed}s)")

            if "deep_analysis" in item:
                try:
                    full = load_json(analysis_path)
                    if full and "commits" in full:
                        for fc in full["commits"]:
                            if fc["sha"] == sha:
                                fc["deep_analysis"] = item["deep_analysis"]
                                if result and result.get("ascend_affected_confirmed") is False:
                                    fc.setdefault("ascend_impact", {})["ascend_affected"] = False
                                break
                        save_json_atomic(analysis_path, full)
                        print(f"  [{i}/{total}] Progress saved to {analysis_path}")

                    if result and result.get("ascend_affected_confirmed") is False:
                        repo_short = repo_dir_name(repo)
                        deltas_data = load_deltas(data_dir, repo_short)
                        if deltas_data and sha in deltas_data.get("deltas", {}):
                            deltas_data["deltas"][sha]["ascend_impact"] = False
                            save_deltas(deltas_path(data_dir, repo_short), deltas_data)
                            print(f"  [{i}/{total}] arch_deltas updated: {sha[:12]} ascend_impact set to false")
                except Exception as e:
                    print(f"  [{i}/{total}] Warning: failed to save progress: {e}")

        finally:
            if original_head:
                print(f"    Restoring vllm source to original HEAD ({original_head[:12]})...")
                subprocess.run(
                    ["git", "checkout", "--force", original_head],
                    cwd=local_repo, capture_output=True, text=True, timeout=60,
                )

    total_elapsed = int(time.time() - total_start)
    print(f"Deep analysis completed for {len(to_analyze)} commits (总耗时 {total_elapsed}s)")
    return True


def get_repo_dir(data_dir, repo):
    return os.path.join(data_dir, repo_dir_name(repo))


def main():
    parser = argparse.ArgumentParser(description="Deep analyze ascend_affected commits via opencode")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/repo)")
    parser.add_argument("--date", required=True, help="Date to analyze (YYYY-MM-DD, UTC+8)")
    parser.add_argument("--local-repo", required=True, help="Path to local repo source code")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    args = parser.parse_args()

    result = deep_analyze_commits(args.repo, args.date, args.data_dir, args.local_repo)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()