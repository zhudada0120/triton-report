#!/usr/bin/env python3
"""
vllm-report MCP Server

Provides 14 tools for AI agents to query vllm-report's knowledge base.

Usage:
  python -m src.mcp_server_app --data-dir /path/to/vllm-report/data --ascend-repo-path /path/to/vllm-ascend

opencode config (~/.config/opencode/opencode.jsonc):
  {
    "mcp": {
      "vllm-report": {
        "type": "local",
        "command": ["python", "-m", "src.mcp_server_app", "--data-dir", "/path/to/vllm-report/data", "--ascend-repo-path", "/path/to/vllm-ascend"],
        "enabled": true
      }
    }
  }
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import anyio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data._track_arch_delta import (
    get_deltas_since_baseline, get_deltas_up_to, get_delta,
    load_deltas, delta_count, get_affected_commits,
)

TZ_CN = timezone(timedelta(hours=8))


# ── Helper functions ──────────────────────────────────────────────

def load_json(filepath: str) -> Optional[dict]:
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_json_atomic(filepath: str, data: dict) -> None:
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


def repo_dir_name(repo_short: str) -> str:
    """Map short repo name to directory name in data/."""
    if repo_short in ("vllm", "vllm-ascend"):
        return repo_short
    # Fallback: try as-is
    return repo_short


def get_analysis_path(data_dir: str, repo: str, date: str) -> str:
    return os.path.join(data_dir, repo_dir_name(repo), "analysis", f"{date}.json")


def get_arch_path(data_dir: str, repo: str) -> str:
    return os.path.join(data_dir, repo_dir_name(repo), "context", "architecture.json")


def get_index_path(data_dir: str, repo: str) -> str:
    return os.path.join(data_dir, repo_dir_name(repo), "index.json")


def get_commits_index_path(data_dir: str, repo: str) -> str:
    """Path to commits-index.json (SHA → {date, message} lookup)."""
    return os.path.join(data_dir, repo_dir_name(repo), "commits-index.json")


def get_adaptation_status_path(data_dir: str) -> str:
    return os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")


def get_baseline_file(ascend_repo_path: str, filename: str) -> Optional[str]:
    """Read a baseline file from vllm-ascend repo."""
    if not ascend_repo_path:
        return None
    filepath = os.path.join(ascend_repo_path, ".github", filename)
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r") as f:
            return f.read().strip()
    except IOError:
        return None


def find_sha_date(data_dir: str, repo: str, sha: str) -> Optional[str]:
    """Find the analysis date for a given SHA by scanning analysis files."""
    analysis_dir = os.path.join(data_dir, repo, "analysis")
    if not os.path.isdir(analysis_dir):
        return None
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
    return None


# ── MCP Server ────────────────────────────────────────────────────

from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("vllm-report")


# ── Tool implementations ──────────────────────────────────────────

def _repo_dir_name(repo: str) -> str:
    """Convert 'vllm' or 'vllm-ascend' to data directory name."""
    if repo in ("vllm", "vllm-ascend"):
        return repo
    return repo.replace("/", "-")


async def tool_get_architecture_context(repo: str) -> str:
    """返回 arch.json（含 knowledge_base）"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    return json.dumps(arch, ensure_ascii=False, indent=2)


async def tool_get_architecture_overview(repo: str) -> str:
    """返回架构概览：overview + modules 列表（名称和路径，不含详情）"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    modules_summary = []
    for m in arch.get("modules", []):
        modules_summary.append({
            "name": m.get("name", ""),
            "path": m.get("path", ""),
            "description": m.get("description", ""),
            "key_classes_count": len(m.get("key_classes", [])),
        })
    return json.dumps({
        "repo": arch.get("repo"),
        "generated_at": arch.get("generated_at"),
        "commit_sha": arch.get("commit_sha"),
        "version_count": len(arch.get("architecture_history", [])),
        "architecture_history": arch.get("architecture_history", []),
        "overview": arch.get("overview"),
        "modules": modules_summary,
        "module_count": len(modules_summary),
    }, ensure_ascii=False, indent=2)


async def tool_get_module_info(repo: str, module_name: str) -> str:
    """返回单个模块的详细信息"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)

    # Search by name (exact match) or path (substring match)
    module = None
    for m in arch.get("modules", []):
        if m.get("name", "").lower() == module_name.lower():
            module = m
            break
    if module is None:
        # Try fuzzy match: name contains module_name
        for m in arch.get("modules", []):
            if module_name.lower() in m.get("name", "").lower():
                module = m
                break
    if module is None:
        # Try path match
        for m in arch.get("modules", []):
            if module_name.lower() in m.get("path", "").lower():
                module = m
                break
    if module is None:
        return json.dumps({"error": f"Module '{module_name}' not found in {repo}"}, ensure_ascii=False)

    return json.dumps(module, ensure_ascii=False, indent=2)


async def tool_get_interface_surface(repo: str) -> str:
    """返回接口面信息：可继承接口列表 + not_used_by_ascend 路径"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    iface = arch.get("interface_surface", {})
    return json.dumps(iface, ensure_ascii=False, indent=2)


async def tool_get_key_abstractions(repo: str) -> str:
    """返回关键抽象列表"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    return json.dumps(arch.get("key_abstractions", []), ensure_ascii=False, indent=2)


async def tool_get_implementation_principles(repo: str) -> str:
    """返回实现原理列表"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    return json.dumps(arch.get("implementation_principles", []), ensure_ascii=False, indent=2)


async def tool_get_hardware_abstraction(repo: str) -> str:
    """返回硬件适配层信息"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    return json.dumps(arch.get("hardware_abstraction", {}), ensure_ascii=False, indent=2)


async def tool_get_development_workflows(repo: str) -> str:
    """返回开发工作流模板"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    kb = arch.get("knowledge_base", {})
    workflows = kb.get("development_workflows", {})
    return json.dumps(workflows, ensure_ascii=False, indent=2)


async def tool_get_testing_guide(repo: str) -> str:
    """返回测试指南"""
    arch_path = get_arch_path(data_dir, repo)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)
    kb = arch.get("knowledge_base", {})
    guide = kb.get("testing_guide", {})
    return json.dumps(guide, ensure_ascii=False, indent=2)


async def tool_get_daily_analysis(repo: str, date: str) -> str:
    """返回指定日期的分析数据"""
    analysis_path = get_analysis_path(data_dir, repo, date)
    analysis = load_json(analysis_path)
    if analysis is None:
        return json.dumps({"error": f"No analysis found for {repo} on {date}"}, ensure_ascii=False)
    return json.dumps(analysis, ensure_ascii=False, indent=2)


async def tool_search_analysis(
    repo: str,
    keywords: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> str:
    """跨日期搜索，按标签/关键词/日期范围过滤"""
    index_path = get_index_path(data_dir, repo)
    index = load_json(index_path)
    if index is None:
        return json.dumps({"error": f"Index not found for {repo}"}, ensure_ascii=False)

    # Collect matching SHAs from keyword_index and tags_index
    matched_shas: set[str] = set()

    if keywords:
        kw_index = index.get("keyword_index", {})
        for kw in keywords:
            kw_lower = kw.lower()
            sha_list = kw_index.get(kw_lower, [])
            matched_shas.update(sha_list)

    if tags:
        tags_index = index.get("tags_index", {})
        for tag in tags:
            sha_list = tags_index.get(tag, [])
            matched_shas.update(sha_list)

    # If no filters, collect all SHAs from keyword_index
    if not keywords and not tags:
        for sha_list in index.get("keyword_index", {}).values():
            matched_shas.update(sha_list)

    # Load commits-index for {date, message} lookup
    commits_index_path = get_commits_index_path(data_dir, repo)
    commits_index = load_json(commits_index_path) or {}

    # Build a quick lookup: sha → date (from commits-index)
    sha_to_date = {}
    for sha, info in commits_index.items():
        if isinstance(info, dict):
            sha_to_date[sha] = info.get("date", "")

    # Filter by date range
    analysis_dates_set = set(index.get("analysis_dates", []))
    if date_from:
        analysis_dates_set = {d for d in analysis_dates_set if d >= date_from}
    if date_to:
        analysis_dates_set = {d for d in analysis_dates_set if d <= date_to}

    # Read analysis files for matched SHAs
    results = []
    seen = set()

    for date in sorted(analysis_dates_set, reverse=True):
        if len(results) >= limit:
            break
        analysis_path = get_analysis_path(data_dir, repo, date)
        analysis = load_json(analysis_path)
        if not analysis:
            continue
        for commit in analysis.get("commits", []):
            sha = commit.get("sha", "")
            if sha in seen:
                continue
            if sha not in matched_shas:
                continue
            seen.add(sha)
            results.append({
                "sha": sha,
                "date": date,
                "message": (commit.get("message", "") or "").split("\n")[0][:120],
                "tags": commit.get("tags", []),
                "ascend_impact": commit.get("ascend_impact"),
                "architecture_impact": commit.get("architecture_impact"),
            })
            if len(results) >= limit:
                break

    return json.dumps({"results": results, "total": len(results)}, ensure_ascii=False, indent=2)


async def tool_get_module_history(repo: str, module_name: str, days: int = 30) -> str:
    """获取某模块的变更历史"""
    index_path = get_index_path(data_dir, repo)
    index = load_json(index_path)
    if index is None:
        return json.dumps({"error": f"Index not found for {repo}"}, ensure_ascii=False)

    modules_index = index.get("modules_index", {})
    sha_list = modules_index.get(module_name, [])
    sha_set = set(sha_list)

    # Load commits-index for {date, message} lookup
    commits_index_path = get_commits_index_path(data_dir, repo)
    commits_index = load_json(commits_index_path) or {}

    # Filter by days
    cutoff = (datetime.now(timezone.utc) + timedelta(hours=8) - timedelta(days=days)).strftime("%Y-%m-%d")

    results = []
    for sha, info in commits_index.items():
        if sha not in sha_set:
            continue
        if not isinstance(info, dict):
            continue
        date = info.get("date", "")
        if date < cutoff:
            continue
        results.append({
            "sha": sha,
            "date": date,
            "message": info.get("msg", ""),
        })

    # Sort by date descending
    results.sort(key=lambda x: x["date"], reverse=True)

    return json.dumps({
        "module": module_name,
        "days": days,
        "total": len(results),
        "commits": results[:100],
    }, ensure_ascii=False, indent=2)


async def tool_get_ascend_impact_summary(repo: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> str:
    """ascend 影响汇总"""
    repo_dir_val = repo_dir_name(repo)
    analysis_dir = os.path.join(data_dir, repo_dir_val, "analysis")
    if not os.path.isdir(analysis_dir):
        return json.dumps({"error": f"Analysis directory not found for {repo}"}, ensure_ascii=False)

    commits_index_path = get_commits_index_path(data_dir, repo)
    commits_index = load_json(commits_index_path) or {}

    results = []
    for fname in sorted(os.listdir(analysis_dir)):
        if not fname.endswith(".json"):
            continue
        date = fname.replace(".json", "")
        if date_from and date < date_from:
            continue
        if date_to and date > date_to:
            continue

        analysis = load_json(os.path.join(analysis_dir, fname))
        if not analysis:
            continue

        for commit in analysis.get("commits", []):
            ascend_impact = commit.get("ascend_impact")
            if ascend_impact and ascend_impact.get("ascend_affected"):
                sha = commit.get("sha", "")
                msg = ""
                ci = commits_index.get(sha, {})
                if isinstance(ci, dict):
                    msg = ci.get("msg", "")
                results.append({
                    "sha": sha,
                    "date": date,
                    "message": msg,
                    "functionality": ascend_impact.get("functionality", ""),
                    "testing": ascend_impact.get("testing", ""),
                    "needs_test_update": ascend_impact.get("needs_test_update", False),
                })

    # Sort by date descending
    results.sort(key=lambda x: x["date"], reverse=True)

    return json.dumps({
        "repo": repo,
        "total_affected": len(results),
        "commits": results[:200],
    }, ensure_ascii=False, indent=2)


async def tool_get_cross_project_mapping() -> str:
    """返回 vllm↔ascend 跨项目映射"""
    # Read ascend arch.json for cross_project_relationship
    arch_path = get_arch_path(data_dir, "vllm-ascend")
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": "Architecture context not found"}, ensure_ascii=False)
    mapping = arch.get("cross_project_relationship", {})
    return json.dumps(mapping, ensure_ascii=False, indent=2)


async def tool_get_patch_catalog(category: Optional[str] = None) -> str:
    """返回 ascend 的 patch 目录"""
    arch_path = get_arch_path(data_dir, "vllm-ascend")
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": "Architecture context not found"}, ensure_ascii=False)

    kb = arch.get("knowledge_base", {})
    catalog = kb.get("patch_catalog", {})

    if category == "platform":
        return json.dumps(catalog.get("platform_patches", []), ensure_ascii=False, indent=2)
    elif category == "worker":
        worker = catalog.get("worker_patches", [])
        v2 = catalog.get("v2_worker_patches", [])
        return json.dumps({"worker_patches": worker, "v2_worker_patches": v2}, ensure_ascii=False, indent=2)
    else:
        return json.dumps(catalog, ensure_ascii=False, indent=2)


async def tool_get_architecture_freshness() -> str:
    """返回 arch.json 的时效性状态"""
    result = {}

    for repo_name in ["vllm", "vllm-ascend"]:
        arch_path = get_arch_path(data_dir, repo_name)
        arch = load_json(arch_path)
        if arch is None:
            result[repo_name] = {"status": "not_found"}
            continue

        gen_time_str = arch.get("generated_at", "")
        commit_sha = arch.get("commit_sha", "unknown")

        info = {
            "commit_sha": commit_sha[:12] if commit_sha != "unknown" else "unknown",
            "generated_at": gen_time_str,
        }

        # Check staleness (older than 7 days)
        if gen_time_str:
            try:
                gen_time = datetime.fromisoformat(gen_time_str)
                now = datetime.now(TZ_CN)
                # Ensure both are timezone-aware for comparison
                if gen_time.tzinfo is None:
                    gen_time = gen_time.replace(tzinfo=TZ_CN)
                days_old = (now - gen_time).days
                info["days_old"] = days_old
                info["is_stale"] = days_old > 7
                if days_old > 7:
                    info["warning"] = f"Architecture context is {days_old} days old, consider refreshing"
            except (ValueError, TypeError):
                info["days_old"] = None
                info["is_stale"] = None

        # Add architecture version history
        arch_history = arch.get("architecture_history", [])
        info["version_count"] = len(arch_history)
        if arch_history:
            info["first_version_sha"] = arch_history[0].get("commit_sha", "")[:12]
            info["first_version_at"] = arch_history[0].get("generated_at", "")
            info["latest_version_sha"] = arch_history[-1].get("commit_sha", "")[:12]

        # Compare with ascend baseline if available
        if ascend_repo_path and repo_name == "vllm":
            main_sha = get_baseline_file(ascend_repo_path, "vllm-main-verified.commit")
            if main_sha and commit_sha != "unknown":
                info["baseline_sha"] = main_sha[:12]
                # Check if baseline is covered by any architecture version
                matches_baseline = any(
                    h.get("commit_sha", "")[:12] == main_sha[:12]
                    for h in arch_history
                ) or commit_sha[:12] == main_sha[:12]
                info["matches_baseline"] = matches_baseline
                if not matches_baseline and arch_history:
                    info["warning"] = (
                        f"Architecture context does not cover baseline commit {main_sha[:12]}. "
                        "The arch knowledge may be outdated relative to the verified baseline."
                    )

        result[repo_name] = info

    return json.dumps(result, ensure_ascii=False, indent=2)


async def tool_get_adaptation_baseline() -> str:
    """返回 vllm-ascend 当前已验证的 vllm 基线"""
    if not ascend_repo_path:
        return json.dumps({"error": "ascend-repo-path not configured, cannot read baseline files"}, ensure_ascii=False)

    main_sha = get_baseline_file(ascend_repo_path, "vllm-main-verified.commit")
    release_tag = get_baseline_file(ascend_repo_path, "vllm-release-tag.commit")

    if not main_sha and not release_tag:
        return json.dumps({"error": "Baseline files not found in ascend repository"}, ensure_ascii=False)

    result = {
        "main_verified_sha": main_sha or "not_found",
        "release_tag": release_tag or "not_found",
    }

    # Count commits after baseline in analysis data
    index_path = get_index_path(data_dir, "vllm")
    index = load_json(index_path)
    if index and main_sha:
        # Find the date of this SHA in analysis data
        baseline_date = None
        for date in index.get("analysis_dates", []):
            analysis_path = get_analysis_path(data_dir, "vllm", date)
            analysis = load_json(analysis_path)
            if analysis:
                for commit in analysis.get("commits", []):
                    if commit.get("sha", "")[:12] == main_sha[:12]:
                        baseline_date = date
                        break
            if baseline_date:
                break

        if baseline_date:
            result["baseline_date"] = baseline_date
            # Count affected commits after baseline
            # tags_index now stores SHA lists, so we need commits-index for dates
            affected = index.get("tags_index", {}).get("ascend_affected", [])
            commits_index_path = get_commits_index_path(data_dir, "vllm")
            commits_index = load_json(commits_index_path) or {}
            after_baseline = 0
            for sha in affected:
                info = commits_index.get(sha)
                if isinstance(info, dict) and info.get("date", "") >= baseline_date:
                    after_baseline += 1
            result["affected_after_baseline"] = after_baseline

            # Read adaptation status for adapted/pending counts
            adaptation_path = get_adaptation_status_path(data_dir)
            adaptation = load_json(adaptation_path)
            if adaptation:
                result["adaptation_stats"] = adaptation.get("stats", {})

    return json.dumps(result, ensure_ascii=False, indent=2)


async def tool_get_commit_diff(repo: str, sha: str) -> str:
    """获取某个 commit 的完整 diff"""
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    commits_dir = os.path.join(repo_dir, "commits")

    # Try to find the commit in local data
    if os.path.isdir(commits_dir):
        for fname in os.listdir(commits_dir):
            if not fname.endswith(".json") or fname in ("meta.json",):
                continue
            date_data = load_json(os.path.join(commits_dir, fname))
            if date_data:
                for commit in date_data.get("commits", []):
                    if commit.get("sha") == sha:
                        patch = commit.get("patch", "") or ""
                        files = commit.get("files", [])
                        if patch:
                            return json.dumps({
                                "sha": sha,
                                "source": "local",
                                "message": commit.get("message", ""),
                                "patch": patch[:50000],  # Truncate if too large
                            }, ensure_ascii=False, indent=2)
                        if files:
                            # Build patch from file patches
                            file_patches = []
                            for f in files:
                                fp = f.get("patch", "")
                                if fp:
                                    file_patches.append({
                                        "filename": f["filename"],
                                        "patch": fp[:20000],
                                    })
                            return json.dumps({
                                "sha": sha,
                                "source": "local",
                                "message": commit.get("message", ""),
                                "file_patches": file_patches,
                            }, ensure_ascii=False, indent=2)

    # Fallback: fetch from GitHub API
    try:
        import urllib.request
        # Map short repo name to owner/repo format for GitHub API
        repo_map = {"vllm": "vllm-project/vllm", "vllm-ascend": "vllm-project/vllm-ascend"}
        gh_repo = repo_map.get(repo, repo)
        api_url = f"https://api.github.com/repos/{gh_repo}/commits/{sha}"
        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github.v3.diff"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            diff = resp.read().decode("utf-8")
        return json.dumps({
            "sha": sha,
            "source": "github_api",
            "diff": diff[:100000],  # Truncate if too large
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch diff: {str(e)}"}, ensure_ascii=False)



async def tool_get_commit_impact_batch(shas: list[str]) -> str:
    """批量查询 commits 的 ascend 影响分析。用于 plan_steps 阶段的确定性路由。

    用 commits-index.json 做 O(1) sha→date 映射, 然后只读对应日期的
    analysis/<date>.json (避免遍历所有日期文件)。

    返回每个 sha 的 (analyzed, ascend_affected, tags, needs_test_update,
    suggested_test_areas)。未在 commits-index.json 中找到的 sha → analyzed=False。
    """
    if not shas:
        return json.dumps({"results": []}, ensure_ascii=False, indent=2)

    commits_index_path = get_commits_index_path(data_dir, "vllm")
    commits_index = load_json(commits_index_path) or {}

    # 按 date 分桶, 一次只读需要的日期文件
    by_date: dict[str, list[str]] = {}
    for sha in shas:
        # commits-index 用完整 sha 做 key
        entry = commits_index.get(sha) or commits_index.get(sha.lower())
        date = entry.get("date") if entry else None
        if date:
            by_date.setdefault(date, []).append(sha)

    # 读对应日期的 analysis 文件, 建 sha → impact 映射
    impacts: dict[str, dict] = {}
    for date, date_shas in by_date.items():
        analysis_path = get_analysis_path(data_dir, "vllm", date)
        analysis = load_json(analysis_path)
        if not analysis:
            continue
        date_set = set(date_shas)
        for commit in analysis.get("commits", []):
            sha = commit.get("sha", "")
            if sha in date_set:
                ai = commit.get("ascend_impact") or {}
                impacts[sha] = {
                    "sha": sha,
                    "analyzed": True,
                    "ascend_affected": bool(ai.get("ascend_affected", False)),
                    "tags": commit.get("tags", []) or [],
                    "needs_test_update": bool(ai.get("needs_test_update", False)),
                    "suggested_test_areas": ai.get("suggested_test_areas", []) or [],
                    "functionality": ai.get("functionality", ""),
                    "testing": ai.get("testing", ""),
                }

    # 组装结果, 按输入顺序; 未找到的 sha → analyzed=False
    results = []
    for sha in shas:
        if sha in impacts:
            results.append(impacts[sha])
        else:
            results.append({"sha": sha, "analyzed": False})

    return json.dumps({"results": results}, ensure_ascii=False, indent=2)


async def tool_get_adaptation_guide(sha: str) -> str:
    """返回某个 commit 的适配指南（markdown 格式）"""
    # Search for this SHA across all analysis files
    for repo in ["vllm"]:
        index_path = get_index_path(data_dir, repo)
        index = load_json(index_path)
        if not index:
            continue

        for date in index.get("analysis_dates", []):
            analysis_path = get_analysis_path(data_dir, repo, date)
            analysis = load_json(analysis_path)
            if not analysis:
                continue
            for commit in analysis.get("commits", []):
                if commit.get("sha") == sha:
                    message = commit.get("message", "").split("\n")[0]
                    comment = commit.get("comment", "")
                    tags = commit.get("tags", [])
                    ascend_impact = commit.get("ascend_impact", {})

                    # Build markdown guide
                    lines = [
                        f"# 适配指南：{sha[:12]}",
                        "",
                        f"**commit 信息：** {message}",
                        f"**日期：** {date}",
                        f"**标签：** {', '.join(tags)}",
                        "",
                        "## 变更分析",
                        comment,
                        "",
                    ]

                    if ascend_impact and ascend_impact.get("ascend_affected"):
                        lines.extend([
                            "## 对 vllm-ascend 的影响",
                            f"- 功能影响：{ascend_impact.get('functionality', '无')}",
                            f"- 测试影响：{ascend_impact.get('testing', '无')}",
                            "",
                            "## 建议适配步骤",
                            "1. 查看 commit diff 了解具体变更",
                            "2. 根据影响分析修改对应 ascend 文件",
                            "3. 运行相关测试验证",
                            "4. 完成后更新适配状态",
                            "",
                        ])

                        if ascend_impact.get("needs_test_update"):
                            areas = ascend_impact.get("suggested_test_areas", [])
                            lines.extend([
                                "## 需要更新的测试",
                                *[f"- {area}" for area in areas],
                                "",
                            ])

                        lines.append("## 测试命令")
                        lines.append("```bash")
                        lines.append("# 单元测试")
                        lines.append("pytest -sv tests/ut/")
                        lines.append("")
                        lines.append("# 端到端测试（需要 NPU 硬件）")
                        lines.append("pytest -sv tests/e2e/pull_request/one_card/")
                        lines.append("```")

                    return "\n".join(lines)

    return f"# 适配指南：{sha[:12]}\n\n未找到该 commit 的分析数据，无法生成适配指南。请先确认该 commit 是否已被分析。"


async def tool_get_pending_adaptations() -> str:
    """获取待适配 (pending) 的 commit 列表"""
    adaptation_path = get_adaptation_status_path(data_dir)
    adaptation = load_json(adaptation_path)
    if adaptation is None:
        return json.dumps({"error": "adaptation-status.json not found. Run track_adaptation.py init first."}, ensure_ascii=False)

    stats = adaptation.get("stats", {})
    pending = [c for c in adaptation.get("commits", []) if c.get("status") == "pending"]

    return json.dumps({
        "stats": stats,
        "pending_count": len(pending),
        "pending_commits": pending[:100],
    }, ensure_ascii=False, indent=2)


def get_lessons_dir(data_dir: str) -> str:
    """Directory holding adaptation-lesson files, one JSON per date."""
    return os.path.join(data_dir, "vllm-ascend", "lessons")


def load_all_lessons(data_dir: str) -> list[dict]:
    """Load every lesson from lessons/<date>.json (newest date first)."""
    lessons_dir = get_lessons_dir(data_dir)
    if not os.path.isdir(lessons_dir):
        return []
    lessons: list[dict] = []
    for fname in sorted(os.listdir(lessons_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(lessons_dir, fname)
        data = load_json(fpath)
        if data and isinstance(data.get("lessons"), list):
            lessons.extend(data["lessons"])
    return lessons


async def tool_get_adaptation_lessons(
    keywords: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    limit: int = 3,
) -> str:
    """获取适配经验（实战沉淀的知识）。按 keywords（子串匹配，忽略大小写）
    和/或 tags 过滤；不传则返回最近的全部。命中会累计 hits 计数。"""
    lessons = load_all_lessons(data_dir)
    if not lessons:
        return json.dumps({"match": False, "lessons": [], "hit_ids": []},
                          ensure_ascii=False, indent=2)

    kw = [k.lower() for k in (keywords or []) if k]
    tags = [t.lower() for t in (tags or []) if t]

    scored: list[tuple[int, dict]] = []
    for lesson in lessons:
        score = 0
        haystack = json.dumps(lesson, ensure_ascii=False).lower()
        if kw:
            for k in kw:
                if k in haystack:
                    score += 2
            if score == 0:
                continue
        if tags:
            lesson_tags = [t.lower() for t in lesson.get("tags", [])]
            tag_hits = sum(1 for t in tags if t in lesson_tags)
            if tag_hits == 0:
                continue
            score += tag_hits
        # Prefer lessons not yet validated (low hits) so fresh lessons surface.
        score -= min(lesson.get("hits", 0), 5)
        scored.append((score, lesson))

    if not scored:
        return json.dumps({"match": False, "lessons": [], "hit_ids": []},
                          ensure_ascii=False, indent=2)

    scored.sort(key=lambda x: -x[0])
    top = scored[:max(1, limit)]
    for _, lesson in top:
        lesson["hits"] = lesson.get("hits", 0) + 1

    return json.dumps({
        "match": True,
        "lessons": [
            {k: v for k, v in l.items() if k != "hits"}
            for _, l in top
        ],
        "hit_ids": [l["id"] for _, l in top],
    }, ensure_ascii=False, indent=2)


def _persist_lesson_to_remote() -> str:
    """Commit + push the lessons change to the remote (best-effort).

    The MCP server usually runs from a short-lived clone (e.g. the main2main
    flow re-creates its vllm-report clone every run), so a lesson that is
    only saved locally would be lost.  Commit with an explicit identity (the
    fresh clone has none — a bare ``git commit`` fails with "Author identity
    unknown") and push with a token-embedded URL when ``GH_TOKEN`` /
    ``GITHUB_TOKEN`` is set: CI runners route github.com through an
    anonymous-fetch proxy that requires the token in the URL for push.
    Falls back to the plain ``origin`` push locally (credential helper).

    Returns "" on success, else an error description.
    """
    repo_root = os.path.dirname(os.path.abspath(data_dir))

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo_root,
                              capture_output=True, text=True)

    try:
        r = _run("status", "--short")
        if r.returncode != 0 or not r.stdout.strip():
            return ""  # nothing to commit
        identity = ("-c", "user.name=vllm-report-bot",
                    "-c", "user.email=vllm-report-bot@users.noreply.github.com")
        r = _run("add", "-A")
        if r.returncode != 0:
            return f"git add failed: {r.stderr.strip()[:200]}"
        msg = f"lessons: {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M')} auto-recorded"
        r = _run(*identity, "commit", "-m", msg)
        if r.returncode != 0:
            return f"git commit failed: {r.stderr.strip()[:200]}"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        targets: list[str] = []
        if token:
            targets.append(
                "https://x-access-token:{token}@gh-proxy.test.osinfra.cn/"
                "https://github.com/vllm-ascend/vllm-report.git".format(token=token))
            targets.append(
                "https://x-access-token:{token}@github.com/"
                "vllm-ascend/vllm-report.git".format(token=token))
        targets.append("origin")
        # Rebase onto the remote before pushing: the daily data-update bot
        # commits to main between our clone and this submit, so a bare push
        # would be rejected as non-fast-forward.  Fetch + rebase keeps our
        # commit on top; on conflict abort and report (the caller can retry).
        fetch_target = targets[0] if targets else "origin"
        r = _run("fetch", fetch_target, "main")
        if r.returncode != 0:
            return f"git fetch failed: {r.stderr.strip()[:200]}"
        r = _run("rebase", "FETCH_HEAD")
        if r.returncode != 0:
            _run("rebase", "--abort")
            return (f"git rebase onto remote failed: "
                    f"{r.stderr.strip()[:200]}")
        last_err = ""
        for target in targets:
            r = _run("push", target, "main")
            if r.returncode == 0:
                return ""
            last_err = r.stderr.strip()[:300]
        return f"git push failed: {last_err}"
    except FileNotFoundError:
        return "git not available"


async def tool_submit_lesson(
    title: str,
    symptom: str,
    root_cause: str,
    fix_guidance: list[str],
    tags: list[str],
    keywords: Optional[list[str]] = None,
    example: Optional[str] = None,
) -> str:
    """提交一条适配经验到 lessons/<date>.json。按当天文件追加，避免并发冲突。

    写文件后自动 commit + push 到远端（见 _persist_lesson_to_remote），
    这样调用方（如 main2main flow 的短生命周期 clone）提交的经验能立即
    固化到仓库，而不是随 clone 销毁而丢失。
    """
    if not title or not root_cause or not fix_guidance:
        return json.dumps({"error": "title, root_cause and fix_guidance are required"},
                          ensure_ascii=False)

    today = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    lessons_dir = get_lessons_dir(data_dir)
    os.makedirs(lessons_dir, exist_ok=True)
    fpath = os.path.join(lessons_dir, f"{today}.json")

    data = load_json(fpath) or {"date": today, "lessons": []}
    existing = data.setdefault("lessons", [])

    # id: L<YYYYMMDD>-NNN (increment within the day's file)
    seq = len(existing) + 1
    lesson = {
        "id": f"L{today.replace('-', '')}-{seq:03d}",
        "title": title,
        "keywords": keywords or [],
        "symptom": symptom,
        "root_cause": root_cause,
        "fix_guidance": fix_guidance,
        "tags": tags,
        "example": example,
        "created_at": datetime.now(TZ_CN).isoformat(),
        "hits": 0,
    }
    if example:
        lesson["example"] = example
    existing.append(lesson)
    save_json_atomic(fpath, data)

    persist_err = _persist_lesson_to_remote()
    resp = {
        "status": "submitted",
        "id": lesson["id"],
        "file": os.path.relpath(fpath, data_dir),
    }
    if persist_err:
        resp["persist_warning"] = persist_err
    return json.dumps(resp, ensure_ascii=False, indent=2)


async def tool_update_adaptation_status(sha: str, status: str, notes: Optional[str] = None) -> str:
    """更新某个 commit 的适配状态（仅支持 pending → adapted）"""
    valid_statuses = ["pending", "adapted"]
    if status not in valid_statuses:
        return json.dumps({"error": f"Invalid status: {status}. Valid: {', '.join(valid_statuses)}"}, ensure_ascii=False)

    adaptation_path = get_adaptation_status_path(data_dir)
    adaptation = load_json(adaptation_path)
    if adaptation is None:
        return json.dumps({"error": "adaptation-status.json not found. Run track_adaptation.py init first."}, ensure_ascii=False)

    found = False
    for commit in adaptation.get("commits", []):
        if commit.get("sha") == sha:
            commit["status"] = status
            if notes:
                commit["adaptation_notes"] = notes
            if status == "adapted":
                commit["adapted_at"] = datetime.now(TZ_CN).isoformat()
            found = True
            break

    if not found:
        return json.dumps({"error": f"Commit {sha[:12]} not found in adaptation tracking"}, ensure_ascii=False)

    # Update stats
    stats = {"total": 0, "pending": 0, "adapted": 0}
    for commit in adaptation.get("commits", []):
        stats["total"] += 1
        s = commit.get("status", "pending")
        if s in stats:
            stats[s] += 1
    adaptation["stats"] = stats

    async def tool_advance_baseline(new_sha: str, message: Optional[str] = None) -> str:
        """推进基线，更新 vllm-ascend 的 vllm-main-verified.commit"""
        if not ascend_repo_path:
            return json.dumps({"error": "ascend-repo-path not configured. Cannot advance baseline."}, ensure_ascii=False)

        baseline_file = os.path.join(ascend_repo_path, ".github", "vllm-main-verified.commit")
        if not os.path.exists(baseline_file):
            return json.dumps({"error": f"Baseline file not found: {baseline_file}"}, ensure_ascii=False)

        try:
            # Read current
            with open(baseline_file, "r") as f:
                current = f.read().strip()

            # Write new SHA
            with open(baseline_file, "w") as f:
                f.write(new_sha.strip() + "\n")

            # Auto-mark newly-covered commits as adapted in adaptation-status.json
            adaptation_path = get_adaptation_status_path(data_dir)
            adaptation = load_json(adaptation_path)
            updated_count = 0
            if adaptation:
                new_baseline_date = find_sha_date(data_dir, "vllm", new_sha)
                for commit in adaptation.get("commits", []):
                    if commit.get("status") == "pending" and new_baseline_date:
                        if commit.get("upstream_date", "") <= new_baseline_date:
                            commit["status"] = "adapted"
                            commit["adapted_at"] = datetime.now(TZ_CN).isoformat()
                            updated_count += 1
                if updated_count > 0:
                    stats = {"total": 0, "pending": 0, "adapted": 0}
                    for commit in adaptation.get("commits", []):
                        stats["total"] += 1
                        s = commit.get("status", "pending")
                        if s in stats:
                            stats[s] += 1
                    adaptation["stats"] = stats
                    adaptation["baseline"]["main_sha"] = new_sha
                    adaptation["baseline"]["baseline_date"] = new_baseline_date or ""
                    save_json_atomic(adaptation_path, adaptation)

            msg = message or f"Advance baseline from {current[:12]} to {new_sha[:12]}"
            return json.dumps({
                "success": True,
                "previous_sha": current,
                "new_sha": new_sha,
                "file_updated": baseline_file,
                "commits_auto_adapted": updated_count,
                "message": msg,
                "note": "File updated locally. Please commit and push to vllm-ascend repository."
            }, ensure_ascii=False, indent=2)
        except IOError as e:
            return json.dumps({"error": f"Failed to update baseline: {str(e)}"}, ensure_ascii=False)


# ── Architecture Delta Tools ──────────────────────────────────────

async def tool_get_architecture_at_commit(repo: str, sha: str) -> str:
    """返回该 commit 发生时的架构知识（基线 + 到该 commit 的增量叠加）"""
    repo_dir_val = repo_dir_name(repo)
    arch_path = get_arch_path(data_dir, repo_dir_val)
    arch = load_json(arch_path)
    if arch is None:
        return json.dumps({"error": f"Architecture context not found for {repo}"}, ensure_ascii=False)

    baseline_sha, deltas = get_deltas_up_to(data_dir, repo_dir_val, sha)

    result = {
        "baseline": {
            "commit_sha": arch.get("commit_sha", "unknown"),
            "generated_at": arch.get("generated_at", "unknown"),
        },
        "target_commit": sha[:12],
        "architecture_snapshot": {
            "overview": arch.get("overview"),
            "modules": arch.get("modules"),
            "key_abstractions": arch.get("key_abstractions"),
            "interface_surface": arch.get("interface_surface"),
            "cross_project_relationship": arch.get("cross_project_relationship"),
        },
        "deltas_since_baseline_up_to_target": [
            {"sha": s, **d} for s, d in deltas
        ],
        "delta_count": len(deltas),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


async def tool_get_architecture_diff(repo: str, sha_from: str, sha_to: str) -> str:
    """返回两个 commit 之间的架构差异"""
    repo_dir_val = repo_dir_name(repo)
    data = load_deltas(data_dir, repo_dir_val)
    if data is None:
        return json.dumps({"error": "Architecture deltas not found"}, ensure_ascii=False)

    all_deltas = data.get("deltas", {})
    baseline_sha = data.get("baseline_sha", "")

    # Sort by date then SHA
    sorted_items = sorted(
        all_deltas.items(),
        key=lambda x: (x[1].get("date", ""), x[0]),
    )

    deltas_between = []
    in_range = False
    for sha, info in sorted_items:
        if sha == sha_from:
            in_range = True
            continue
        if in_range:
            deltas_between.append({"sha": sha, **all_deltas[sha]})
        if sha == sha_to:
            break

    return json.dumps({
        "baseline_sha": baseline_sha,
        "sha_from": sha_from[:12],
        "sha_to": sha_to[:12],
        "deltas_between": deltas_between,
        "delta_count": len(deltas_between),
    }, ensure_ascii=False, indent=2)


async def tool_get_adaptation_roadmap(repo: str, sha_from: str, sha_to: str) -> str:
    """返回从 sha_from 到 sha_to 的完整适配路线"""
    repo_dir_val = repo_dir_name(repo)
    data = load_deltas(data_dir, repo_dir_val)
    if data is None:
        return json.dumps({"error": "Architecture deltas not found"}, ensure_ascii=False)

    all_deltas = data.get("deltas", {})
    baseline_sha = data.get("baseline_sha", "")

    sorted_items = sorted(
        all_deltas.items(),
        key=lambda x: (x[1].get("date", ""), x[0]),
    )

    commits_to_adapt = []
    in_range = False
    for sha, info in sorted_items:
        if sha == sha_from:
            in_range = True
            continue
        if not in_range:
            continue
        info = all_deltas[sha]
        commits_to_adapt.append({
            "sha": sha[:12],
            "message": info.get("change_summary", ""),
            "affected_modules": info.get("affected_modules", []),
            "ascend_impact": info.get("ascend_impact", False),
        })
        if sha == sha_to:
            break

    affected_modules = set()
    ascend_affected_count = 0
    for c in commits_to_adapt:
        affected_modules.update(c["affected_modules"])
        if c["ascend_impact"]:
            ascend_affected_count += 1

    # Fetch adaptation status for these commits
    adaptation_status = {}
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")
    adapt_data = load_json(adaptation_path)
    if adapt_data:
        for ac in adapt_data.get("commits", []):
            adaptation_status[ac["sha"][:12]] = ac.get("status", "unknown")

    for c in commits_to_adapt:
        c["adaptation_status"] = adaptation_status.get(c["sha"], "not_tracked")

    return json.dumps({
        "baseline_sha": baseline_sha[:12] if baseline_sha else "unknown",
        "sha_from": sha_from[:12],
        "sha_to": sha_to[:12],
        "total_commits": len(commits_to_adapt),
        "ascend_affected_count": ascend_affected_count,
        "affected_modules": sorted(affected_modules),
        "commits_to_adapt": commits_to_adapt,
    }, ensure_ascii=False, indent=2)


async def tool_get_commit_arch_delta(repo: str, sha: str) -> str:
    """返回单个 commit 对架构的增量影响"""
    repo_dir_val = repo_dir_name(repo)
    delta = get_delta(data_dir, repo_dir_val, sha)
    if delta is None:
        return json.dumps({"error": f"No architecture delta found for {sha[:12]}"}, ensure_ascii=False)
    return json.dumps({
        "sha": sha[:12],
        "delta": delta,
    }, ensure_ascii=False, indent=2)


# ── MCP Tool Definitions ──────────────────────────────────────────

from mcp.types import Tool

TOOLS = [
    Tool(
        name="get_architecture_context",
        description="[Full] Return all architecture.json data for a repo (modules, abstractions, knowledge_base, etc.). Use when you need the complete picture. For targeted queries prefer get_architecture_overview, get_module_info, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_architecture_overview",
        description="[Progressive] Return overview + modules list (name, path, description) without full details. Use this as your first call to get the lay of the land.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_module_info",
        description="[Progressive] Return detailed info for a single module by name. Supports fuzzy matching. E.g. 'attention', 'scheduler', 'platform'.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "module_name": {
                    "type": "string",
                    "description": "Module name (fuzzy match supported, e.g. 'attention', 'scheduler', 'platform')",
                },
            },
            "required": ["repo", "module_name"],
        },
    ),
    Tool(
        name="get_interface_surface",
        description="[Progressive] Return interface surface: inheritable_interfaces list + not_used_by_ascend path list. Use when you need to understand which interfaces ascend inherits/overrides.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_key_abstractions",
        description="[Progressive] Return key abstractions/classes with inheritance info, key methods, and ascend implementations. Use when you need deep class-level understanding.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_implementation_principles",
        description="[Progressive] Return implementation principles covering core workflows and design decisions. Use when you need to understand how a module works internally.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_hardware_abstraction",
        description="[Progressive] Return hardware abstraction layer info: platform-independent vs platform-specific interfaces.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_development_workflows",
        description="[Progressive] Return development workflow templates for adding patches, models, env vars, attention backends, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_testing_guide",
        description="[Progressive] Return testing guide with test commands, environment setup, and lint commands for both vllm and vllm-ascend.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                }
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_daily_analysis",
        description="Return analysis data for a specific date and repo",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "date": {
                    "type": "string",
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "Date in YYYY-MM-DD format",
                },
            },
            "required": ["repo", "date"],
        },
    ),
    Tool(
        name="search_analysis",
        description="Cross-date search with keyword/tag/date filters",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search keywords (matched against commit messages)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (e.g. high-risk, ascend_affected, attention)",
                },
                "date_from": {
                    "type": "string",
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "Start date (inclusive)",
                },
                "date_to": {
                    "type": "string",
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "End date (inclusive)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                    "default": 50,
                },
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_module_history",
        description="Get change history for a specific module",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "module_name": {
                    "type": "string",
                    "description": "Module name (e.g. attention, scheduler, platform)",
                },
                "days": {
                    "type": "integer",
                    "description": "How many days back to look (default 30)",
                    "default": 30,
                },
            },
            "required": ["repo", "module_name"],
        },
    ),
    Tool(
        name="get_ascend_impact_summary",
        description="Summary of commits that affect vllm-ascend",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "date_from": {
                    "type": "string",
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "Start date (inclusive)",
                },
                "date_to": {
                    "type": "string",
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "End date (inclusive)",
                },
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="get_cross_project_mapping",
        description="Return vllm ↔ vllm-ascend cross-project mapping",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_patch_catalog",
        description="Return vllm-ascend patch catalog (all patches or filtered by category)",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["platform", "worker"],
                    "description": "Filter by patch category (optional)",
                }
            },
        },
    ),
    Tool(
        name="get_architecture_freshness",
        description="Check if architecture.json is up-to-date",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_adaptation_baseline",
        description="Return current vllm baseline verified by vllm-ascend",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_commit_diff",
        description="Get full diff for a commit (local data first, GitHub API fallback)",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "sha": {
                    "type": "string",
                    "description": "Full commit SHA",
                },
            },
            "required": ["repo", "sha"],
        },
    ),
    Tool(
        name="get_adaptation_guide",
        description="Get adaptation guide for a commit (markdown format)",
        inputSchema={
            "type": "object",
            "properties": {
                "sha": {
                    "type": "string",
                    "description": "Full commit SHA",
                }
            },
            "required": ["sha"],
        },
    ),
    Tool(
        name="get_commit_impact_batch",
        description="Batch query ascend-impact analysis for a list of vllm commits (JSON, structured). Used by plan_steps for deterministic routing.",
        inputSchema={
            "type": "object",
            "properties": {
                "shas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full vllm commit SHAs to query",
                }
            },
            "required": ["shas"],
        },
    ),
    Tool(
        name="get_pending_adaptations",
        description="Get list of pending/unknown adaptation commits",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="advance_baseline",
        description="Advance the vllm baseline (update vllm-main-verified.commit)",
        inputSchema={
            "type": "object",
            "properties": {
                "new_sha": {
                    "type": "string",
                    "description": "New vllm commit SHA to set as verified baseline",
                },
                "message": {
                    "type": "string",
                    "description": "Optional commit message for the baseline update",
                },
            },
            "required": ["new_sha"],
        },
    ),
    # ── Architecture Delta Tools ────────────────────────────────
    Tool(
        name="get_architecture_at_commit",
        description="返回该 commit 发生时的架构知识（基线 + 到该 commit 的增量叠加）",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "sha": {
                    "type": "string",
                    "description": "Target commit SHA (full or first 12 chars)",
                },
            },
            "required": ["repo", "sha"],
        },
    ),
    Tool(
        name="get_architecture_diff",
        description="返回两个 commit 之间的架构差异",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "sha_from": {
                    "type": "string",
                    "description": "Start commit SHA",
                },
                "sha_to": {
                    "type": "string",
                    "description": "End commit SHA",
                },
            },
            "required": ["repo", "sha_from", "sha_to"],
        },
    ),
    Tool(
        name="get_adaptation_roadmap",
        description="返回从 sha_from 到 sha_to 的完整适配路线（含适配状态）",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "sha_from": {
                    "type": "string",
                    "description": "Start commit SHA (e.g. baseline)",
                },
                "sha_to": {
                    "type": "string",
                    "description": "End commit SHA (e.g. target)",
                },
            },
            "required": ["repo", "sha_from", "sha_to"],
        },
    ),
    Tool(
        name="get_commit_arch_delta",
        description="返回单个 commit 对架构的增量影响",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["vllm", "vllm-ascend"],
                    "description": "Repository name",
                },
                "sha": {
                    "type": "string",
                    "description": "Commit SHA",
                },
            },
            "required": ["repo", "sha"],
        },
    ),
    Tool(
        name="get_adaptation_lessons",
        description="获取适配经验（实战沉淀的知识）。按 keywords（子串匹配）和/或 tags 过滤；返回最相关的几条含 symptom/root_cause/fix_guidance。适配或修复前先查这里，命中则直接按经验执行。",
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "检索关键词（错误消息、主题词等，子串匹配忽略大小写）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分类标签过滤（multimodal / cache-path / attention / worker / e2e-fix 等）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数（默认 3）",
                },
            },
        },
    ),
    Tool(
        name="submit_lesson",
        description="提交一条适配经验到 lessons 库（实战沉淀：E2E 反复失败后修好的陷阱、新发现的适配模式）。title/root_cause/fix_guidance 必填。",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "经验的一句话标题",
                },
                "symptom": {
                    "type": "string",
                    "description": "现象（什么情况下触发）",
                },
                "root_cause": {
                    "type": "string",
                    "description": "根因",
                },
                "fix_guidance": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "修复步骤（可执行的操作列表）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分类标签（multimodal / cache-path / attention / worker / e2e-fix 等）",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "检索关键词（错误消息关键字，供 get_adaptation_lessons 匹配）",
                },
                "example": {
                    "type": "string",
                    "description": "具体案例（可选）",
                },
            },
            "required": ["title", "symptom", "root_cause", "fix_guidance", "tags"],
        },
    ),
]


# ── MCP v2.0 Server ──────────────────────────────────────────────

from mcp.types import (
    ListToolsRequest, CallToolRequest, CallToolRequestParams,
    ListToolsResult, CallToolResult,
    TextContent,
)
from mcp.types import PaginatedRequestParams


async def handle_list_tools(ctx, params) -> ListToolsResult:
    """Handler for tools/list. ctx is ServerRequestContext, params is PaginatedRequestParams."""
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
    """Handler for tools/call. ctx is ServerRequestContext, params is CallToolRequestParams."""
    name = params.name
    args = params.arguments or {}

    try:
        if name == "get_architecture_context":
            result = await tool_get_architecture_context(args["repo"])
        elif name == "get_architecture_overview":
            result = await tool_get_architecture_overview(args["repo"])
        elif name == "get_module_info":
            result = await tool_get_module_info(args["repo"], args["module_name"])
        elif name == "get_interface_surface":
            result = await tool_get_interface_surface(args["repo"])
        elif name == "get_key_abstractions":
            result = await tool_get_key_abstractions(args["repo"])
        elif name == "get_implementation_principles":
            result = await tool_get_implementation_principles(args["repo"])
        elif name == "get_hardware_abstraction":
            result = await tool_get_hardware_abstraction(args["repo"])
        elif name == "get_development_workflows":
            result = await tool_get_development_workflows(args["repo"])
        elif name == "get_testing_guide":
            result = await tool_get_testing_guide(args["repo"])
        elif name == "get_daily_analysis":
            result = await tool_get_daily_analysis(args["repo"], args["date"])
        elif name == "search_analysis":
            result = await tool_search_analysis(
                args["repo"],
                keywords=args.get("keywords"),
                tags=args.get("tags"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                limit=args.get("limit", 50),
            )
        elif name == "get_module_history":
            result = await tool_get_module_history(args["repo"], args["module_name"], args.get("days", 30))
        elif name == "get_ascend_impact_summary":
            result = await tool_get_ascend_impact_summary(args["repo"], args.get("date_from"), args.get("date_to"))
        elif name == "get_cross_project_mapping":
            result = await tool_get_cross_project_mapping()
        elif name == "get_patch_catalog":
            result = await tool_get_patch_catalog(args.get("category"))
        elif name == "get_architecture_freshness":
            result = await tool_get_architecture_freshness()
        elif name == "get_adaptation_baseline":
            result = await tool_get_adaptation_baseline()
        elif name == "get_commit_diff":
            result = await tool_get_commit_diff(args["repo"], args["sha"])
        elif name == "get_commit_impact_batch":
            result = await tool_get_commit_impact_batch(args["shas"])
        elif name == "get_adaptation_guide":
            result = await tool_get_adaptation_guide(args["sha"])
        elif name == "get_pending_adaptations":
            result = await tool_get_pending_adaptations()
        elif name == "advance_baseline":
            result = await tool_advance_baseline(args["new_sha"], args.get("message"))
        elif name == "get_architecture_at_commit":
            result = await tool_get_architecture_at_commit(args["repo"], args["sha"])
        elif name == "get_architecture_diff":
            result = await tool_get_architecture_diff(args["repo"], args["sha_from"], args["sha_to"])
        elif name == "get_adaptation_roadmap":
            result = await tool_get_adaptation_roadmap(args["repo"], args["sha_from"], args["sha_to"])
        elif name == "get_commit_arch_delta":
            result = await tool_get_commit_arch_delta(args["repo"], args["sha"])
        elif name == "get_adaptation_lessons":
            result = await tool_get_adaptation_lessons(
                args.get("keywords"), args.get("tags"), args.get("limit", 3))
        elif name == "submit_lesson":
            result = await tool_submit_lesson(
                args["title"], args.get("symptom", ""), args["root_cause"],
                args["fix_guidance"], args["tags"], args.get("keywords"),
                args.get("example"))
        else:
            result = json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        result = json.dumps({"error": f"Tool {name} failed: {str(e)}"})

    return CallToolResult(content=[TextContent(type="text", text=result)])


# ── Main ──────────────────────────────────────────────────────────

def main():
    global data_dir, ascend_repo_path
    parser = argparse.ArgumentParser(description="vllm-report MCP Server")
    parser.add_argument(
        "--data-dir", required=True,
        help="Path to vllm-report data directory"
    )
    parser.add_argument(
        "--ascend-repo-path", default=None,
        help="Path to vllm-ascend repository (for baseline tracking)"
    )
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    ascend_repo_path = os.path.abspath(args.ascend_repo_path) if args.ascend_repo_path else None

    if not os.path.isdir(data_dir):
        print(f"Error: data directory not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    # Set up MCP server
    server.add_request_handler("tools/list", PaginatedRequestParams, handle_list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)

    # Run with stdio transport

    async def run_server():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run_server)


if __name__ == "__main__":
    main()