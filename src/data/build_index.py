#!/usr/bin/env python3
"""
Build search index for each repo.

Generates two files per repo:
  1. data/{repo}/index.json — lightweight index mapping (tags/modules/keywords → SHA list)
  2. data/{repo}/commits-index.json — SHA → {date, message} lookup table

The two-layer design avoids redundant storage of commit metadata across
multiple index entries (which caused index.json to grow to ~1MB+).

Usage:
  python src/data/build_index.py --data-dir data
  python src/data/build_index.py --data-dir data --ascend-repo-path /path/to/vllm-ascend
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data._source_repo import repo_dir_name
from data._track_arch_delta import load_deltas, delta_count, get_affected_commits

TZ_CN = timezone(timedelta(hours=8))

REPO_MAP = {
    "vllm": "vllm-project/vllm",
    "vllm-ascend": "vllm-project/vllm-ascend",
}

# Tags that are NOT module tags (these are type/risk indicators)
NON_MODULE_TAGS = {
    "feature", "bugfix", "refactor", "performance", "docs", "test",
    "chore", "ci", "revert", "unknown-risk",
    "high-risk", "medium-risk", "low-risk",
}


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  Warning: Failed to load {filepath}: {e}")
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


def repo_dir_name(repo):
    """Convert 'owner/repo' to the directory name used in data/."""
    return repo.replace("/", "-")


def get_analysis_dates(data_dir, repo_dir_name_val):
    """Get sorted list of analysis dates for a repo."""
    repo_dir = os.path.join(data_dir, repo_dir_name_val)
    analysis_dir = os.path.join(repo_dir, "analysis")
    if not os.path.isdir(analysis_dir):
        return []
    dates = sorted(
        f.replace(".json", "")
        for f in os.listdir(analysis_dir)
        if f.endswith(".json") and f != ".gitkeep"
    )
    return dates


def tokenize(text):
    if not text:
        return set()
    text_lower = text.lower()
    lines = text_lower.split("\n")
    filtered_lines = []
    trailer_prefixes = (
        "signed-off-by", "co-authored-by", "reviewed-by", "acked-by",
        "reported-by", "tested-by", "suggested-by",
    )
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(p) for p in trailer_prefixes):
            continue
        filtered_lines.append(stripped)
    clean_text = " ".join(filtered_lines)
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", clean_text)
    sha_pattern = re.compile(r"^[0-9a-f]{7,40}$")
    tokens = [t for t in tokens if not sha_pattern.match(t)]
    stop_words = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "on",
        "is", "it", "as", "at", "by", "with", "from", "be", "not", "are",
        "was", "were", "has", "have", "had", "do", "does", "did", "but",
        "if", "no", "so", "up", "out", "all", "just", "about", "can",
        "this", "that", "use", "fix", "add", "set", "get", "make", "refactor",
        "how", "why", "which", "where", "when", "what", "who", "whom",
        "after", "before", "during", "since", "until", "while",
        "within", "without", "through", "because", "therefore",
        "however", "although", "though", "instead",
        "using", "based", "following", "including", "regarding",
        "also", "too", "very", "just", "only", "more", "less", "most",
        "here", "there", "where", "hence", "thus", "then",
        "same", "other", "another", "different", "both", "each", "every",
        "well", "already", "still", "yet", "even", "ever", "never",
        "much", "many", "few", "some", "any", "all", "both",
        "such", "than", "their", "them", "its", "our", "your", "her", "his",
        "may", "might", "can", "could", "will", "would", "shall", "should",
        "added", "removed", "changed", "updated", "fixed", "introduced",
        "adds", "removes", "changes", "updates", "fixes", "introduces",
        "adding", "removing", "changing", "updating", "fixing", "introducing",
        "implementation", "implementations", "implement", "implements", "implemented",
        "support", "supports", "supported", "supporting",
        "enable", "enabled", "enables", "enabling",
        "allow", "allows", "allowed", "allowing",
        "handle", "handles", "handled", "handling",
        "prevent", "prevents", "prevented", "preventing",
        "ensure", "ensures", "ensured", "ensuring",
        "require", "requires", "required", "requiring",
        "provide", "provides", "provided", "providing",
        "improve", "improves", "improved", "improving",
        "optimize", "optimizes", "optimized", "optimizing",
        "introduce", "introduces", "introduced", "introducing",
        "caused", "cause", "causes", "causing",
        "commit", "commits", "committed", "committing",
        "issue", "issues", "problem", "problems", "result", "results",
        "reason", "reasons", "solution", "solutions", "method", "methods",
        "approach", "approaches", "purpose", "detail", "details",
        "simple", "basic", "major", "minor", "multiple", "single",
        "common", "standard", "typical", "specific", "various",
        "overall", "current", "existing", "previous", "subsequent",
        "available", "necessary", "possible", "potential",
    }
    return {t for t in tokens if len(t) > 2 and t not in stop_words}


def build_index(data_dir, repo_dir_name_val, ascend_repo_path=None):
    """Build or update index for a single repo.

    Produces two files:
      - index.json: lightweight tag/module/keyword → SHA list
      - commits-index.json: SHA → {date, message} for fast lookup
    """
    repo_dir = os.path.join(data_dir, repo_dir_name_val)
    dates = get_analysis_dates(data_dir, repo_dir_name_val)

    if not dates:
        print(f"  No analysis files found for {repo_dir_name_val}, skipping")
        return False

    print(f"  Building index for {repo_dir_name_val} ({len(dates)} dates)...")

    # ── Phase 1: collect all commits ──────────────────────────────────
    # commits_map: sha → {date, message}
    # tags_index: tag → set of shas
    # modules_index: module_tag → set of shas
    # keyword_index: keyword → set of shas
    # architecture_impact_index: sha → list of affected interfaces

    tags_index = {}
    modules_index = {}
    keyword_index = {}
    architecture_impact_index = {}
    commits_map = {}

    for date in dates:
        analysis_path = os.path.join(repo_dir, "analysis", f"{date}.json")
        analysis = load_json(analysis_path)
        if not analysis:
            continue

        for commit in analysis.get("commits", []):
            sha = commit.get("sha", "")
            if not sha:
                continue
            message = commit.get("message", "") or ""
            tags = commit.get("tags", [])

            # Store commit metadata once (deduplicated by sha)
            if sha not in commits_map:
                commits_map[sha] = {
                    "date": date,
                    "msg": message.split("\n")[0][:120],
                }

            # Build tags_index — store only SHA strings
            for tag in tags:
                tags_index.setdefault(tag, set()).add(sha)
                if tag not in NON_MODULE_TAGS:
                    modules_index.setdefault(tag, set()).add(sha)

            # Build keyword_index — store only SHA strings
            tokens = tokenize(message)
            for token in tokens:
                keyword_index.setdefault(token, set()).add(sha)

            # Build architecture_impact_index — sha -> {affected_interfaces, date}
            arch_impact = commit.get("architecture_impact")
            if arch_impact and arch_impact.get("affects_architecture"):
                architecture_impact_index[sha] = {
                    "date": date,
                    "affected_interfaces": arch_impact.get("affected_interfaces", []),
                }

    # Convert sets to sorted lists for deterministic JSON output
    tags_index = {k: sorted(v) for k, v in tags_index.items()}
    modules_index = {k: sorted(v) for k, v in modules_index.items()}
    keyword_index = {k: sorted(v) for k, v in keyword_index.items()}

    # Check available data
    context_path = os.path.join(repo_dir, "context", "architecture.json")
    arch_exists = os.path.exists(context_path)
    has_adaptation = os.path.exists(os.path.join(data_dir, "vllm-ascend", "adaptation-status.json"))

    # Build adaptation_baseline reference (from vllm-ascend source files)
    adaptation_baseline = {
        "source": "vllm-ascend/.github/vllm-main-verified.commit",
        "release_tag_source": "vllm-ascend/.github/vllm-release-tag.commit",
    }
    if ascend_repo_path:
        main_verified = os.path.join(ascend_repo_path, ".github", "vllm-main-verified.commit")
        release_tag = os.path.join(ascend_repo_path, ".github", "vllm-release-tag.commit")
        if os.path.exists(main_verified):
            with open(main_verified, "r") as f:
                adaptation_baseline["current_sha"] = f.read().strip()
        if os.path.exists(release_tag):
            with open(release_tag, "r") as f:
                adaptation_baseline["current_release_tag"] = f.read().strip()

    # Record architecture version info
    context_path = os.path.join(repo_dir, "context", "architecture.json")
    arch_version = {}
    context = load_json(context_path)
    if context:
        arch_version = {
            "commit_sha": context.get("commit_sha", "unknown"),
            "generated_at": context.get("generated_at", "unknown"),
            "version_count": len(context.get("architecture_history", [])),
        }

    # Record architecture delta info
    arch_deltas_info = {}
    deltas_data = load_deltas(data_dir, repo_dir_name_val)
    if deltas_data:
        arch_deltas_info = {
            "baseline_sha": deltas_data.get("baseline_sha", ""),
            "delta_count": delta_count(data_dir, repo_dir_name_val),
            "affected_commits": get_affected_commits(data_dir, repo_dir_name_val),
        }

    # ── Phase 2: write lightweight index.json ──────────────────────────
    index = {
        "repo": repo_dir_name_val,
        "source_repo": REPO_MAP.get(repo_dir_name_val, repo_dir_name_val),
        "built_at": datetime.now(TZ_CN).isoformat(),
        "total_dates": len(dates),
        "date_range": [dates[0], dates[-1]] if dates else [],
        "analysis_dates": dates,
        "available_data": {
            "architecture_context": arch_exists,
            "analysis_files": len(dates) > 0,
            "adaptation_status": has_adaptation,
        },
        "architecture_version": arch_version,
        "architecture_deltas": arch_deltas_info,
        "tags_index": tags_index,
        "modules_index": modules_index,
        "architecture_impact_index": architecture_impact_index,
        "keyword_index": keyword_index,
        "adaptation_baseline": adaptation_baseline,
    }

    index_path = os.path.join(repo_dir, "index.json")
    save_json_atomic(index_path, index)
    index_size = len(json.dumps(index))
    print(f"  index.json saved ({index_size:,} bytes)")

    # ── Phase 3: write commits-index.json ──────────────────────────────
    # Sort commits by date descending for convenience
    sorted_commits = dict(sorted(
        commits_map.items(),
        key=lambda x: x[1]["date"],
        reverse=True,
    ))
    commits_index_path = os.path.join(repo_dir, "commits-index.json")
    save_json_atomic(commits_index_path, sorted_commits)
    ci_size = len(json.dumps(sorted_commits))
    print(f"  commits-index.json saved ({ci_size:,} bytes, {len(sorted_commits)} commits)")

    total_size = index_size + ci_size
    print(f"  Total: {total_size:,} bytes (was ~{index_size + ci_size * 2:,} bytes with old format)")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Build search index for vllm-report data"
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Data directory (default: data)"
    )
    parser.add_argument(
        "--ascend-repo-path", default=None,
        help="Path to vllm-ascend repository (for baseline tracking)"
    )
    args = parser.parse_args()

    repos = [("vllm-project/vllm", "vllm"), ("vllm-project/vllm-ascend", "vllm-ascend")]
    success = True

    for repo_full, repo_short in repos:
        result = build_index(args.data_dir, repo_short, args.ascend_repo_path)
        if not result:
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()