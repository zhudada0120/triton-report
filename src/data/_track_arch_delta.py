#!/usr/bin/env python3
"""
Architecture delta tracking for vllm-report.

Tracks per-commit changes to the architecture knowledge base.
When architecture.json is regenerated (user-initiated), deltas are reset.

Data model:
  arch_deltas.json
  ├── baseline_sha: str           # commit SHA of the architecture.json this is based on
  ├── baseline_generated_at: str  # when the baseline was generated
  └── deltas: {
        "<commit_sha>": {
          "affected_modules": ["module1", "module2"],
          "affected_interfaces": ["InterfaceName"],
          "interface_changes": "description of what changed",
          "change_summary": "short summary",
          "ascend_impact": true|false,
        }
      }
"""
import json
import os
import tempfile

DELTAS_FILENAME = os.path.join("context", "arch_deltas.json")


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


def deltas_path(data_dir, repo_dir_name_val):
    return os.path.join(data_dir, repo_dir_name_val, DELTAS_FILENAME)


def load_deltas(data_dir, repo_dir_name_val):
    path = deltas_path(data_dir, repo_dir_name_val)
    return load_json(path)


def init_deltas(data_dir, repo_dir_name_val, baseline_sha, baseline_generated_at):
    path = deltas_path(data_dir, repo_dir_name_val)
    data = {
        "baseline_sha": baseline_sha,
        "baseline_generated_at": baseline_generated_at,
        "deltas": {},
    }
    save_json_atomic(path, data)
    return data


def reset_deltas(data_dir, repo_dir_name_val, new_baseline_sha, new_baseline_generated_at):
    init_deltas(data_dir, repo_dir_name_val, new_baseline_sha, new_baseline_generated_at)


def add_delta(data_dir, repo_dir_name_val, commit_sha, delta_info):
    path = deltas_path(data_dir, repo_dir_name_val)
    data = load_json(path)
    if data is None:
        return False
    if "deltas" not in data:
        data["deltas"] = {}
    data["deltas"][commit_sha] = delta_info
    save_json_atomic(path, data)
    return True


def get_delta(data_dir, repo_dir_name_val, commit_sha):
    data = load_deltas(data_dir, repo_dir_name_val)
    if data is None:
        return None
    return data.get("deltas", {}).get(commit_sha)


def get_deltas_since_baseline(data_dir, repo_dir_name_val):
    """Return all deltas sorted by date then SHA (oldest first)."""
    data = load_deltas(data_dir, repo_dir_name_val)
    if data is None:
        return None, []
    all_deltas = data.get("deltas", {})
    sorted_items = sorted(
        all_deltas.items(),
        key=lambda x: (x[1].get("date", ""), x[0]),
    )
    return data.get("baseline_sha"), sorted_items


def get_deltas_up_to(data_dir, repo_dir_name_val, target_sha):
    """Return baseline info + deltas for commits up to target_sha (inclusive).

    Deltas are sorted by date then by commit order.
    """
    data = load_deltas(data_dir, repo_dir_name_val)
    if data is None:
        return None, []

    baseline_sha = data.get("baseline_sha")
    all_deltas = data.get("deltas", {})

    # Sort by date (ascending), then by SHA for deterministic order
    sorted_items = sorted(
        all_deltas.items(),
        key=lambda x: (x[1].get("date", ""), x[0]),
    )

    result = []
    for sha, info in sorted_items:
        result.append((sha, info))
        if sha == target_sha:
            break
            break

    return baseline_sha, result


def get_affected_commits(data_dir, repo_dir_name_val):
    """Return all commit SHAs that have deltas with ascend_impact=true."""
    data = load_deltas(data_dir, repo_dir_name_val)
    if data is None:
        return []
    return [
        sha for sha, info in data.get("deltas", {}).items()
        if info.get("ascend_impact")
    ]


def delta_count(data_dir, repo_dir_name_val):
    data = load_deltas(data_dir, repo_dir_name_val)
    if data is None:
        return 0
    return len(data.get("deltas", {}))