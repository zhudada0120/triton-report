# vllm-report

Daily commit monitor and AI analysis for [vllm](https://github.com/vllm-project/vllm) and [vllm-ascend](https://github.com/vllm-project/vllm-ascend). Provides a knowledge base for AI agents to support vllm-ascend code upgrades and main2main adaptation.

## Features

- **Daily Commit Fetching** — Automatically fetches new commits (with full diff) via GitHub Actions daily at 02:00 CST
- **Two-Phase AI Analysis** —
  - **Phase 1**: Bulk analysis via DeepSeek API (intent, risk, ascend impact, test impact) with path-based triage that auto-skips non-ascend-relevant commits (tests/docs/CI/platform-specific code), reducing LLM API costs
  - **Phase 2**: Deep analysis via opencode agent for ascend_affected commits — identifies specific affected interfaces, adaptation effort, and adaptation guide by reading actual source files (using the AST-extracted source context cache)
- **Diff-Aware Architecture Impact** — Commits modifying key interface files get `architecture_impact` markers (affected_interfaces, recommend_refresh), detected automatically from file paths against cross-project relationship rules
- **Architecture Context Cache** — Auto-generated project architecture summaries via opencode agent (on demand or via the `refresh-context.yml` workflow), injected into AI analysis prompts for precise impact judgment
- **Knowledge Base** — `knowledge_base` field in architecture.json includes patch catalog, development workflows, and testing guide
- **Patch Catalog Extraction** — Deterministic regex-based parsing of vllm-ascend's `patch/__init__.py`, producing structured JSON without LLM costs
- **Source Context Cache** — AST-based extraction of public interfaces from source files used in deep analysis, so Phase 2 needs no extra LLM calls to re-read file structure
- **Search Index** — Keyword/tag/module index for fast cross-date search
- **Adaptation Status Tracking** — Automatically tracks which vllm upstream commits need adaptation, with only two states: `pending` (needs work) and `adapted` (covered by baseline). Regenerated on every fetch+analyze cycle.
- **Adaptation Lessons** — Practical adaptation knowledge auto-recorded from main2main runs (recurring E2E fix failures, new patterns) and queried by AI agents to fix failures in one pass
- **MCP Server** — Stdio-based MCP server with 28 tools for AI agents to query the knowledge base. Supports progressive loading — agents can start with a lightweight overview and drill into specific modules as needed
- **Static Web Dashboard** — Dark-themed monitor page with commit list, diff viewer, AI analysis overlay, and adaptation status filtering
- **Data Lifecycle Management** — Stale data cleanup removes commit data without corresponding analysis

## Project Structure

```
vllm-report/
├── .github/
│   ├── workflows/
│   │   ├── daily-commit.yml          # Daily fetch + AI analysis
│   │   ├── refresh-context.yml       # On-demand architecture context (re)generation
│   │   └── pages.yml                 # GitHub Pages deployment
│   └── scripts/
│       └── clean_stale_data.py       # Data cleanup (CI only)
├── data/
│   ├── README.json                   # Entry point for AI agents
│   ├── vllm/                         # vllm project data
│   │   ├── meta.json
│   │   ├── commits/                  # Daily commit JSON files
│   │   ├── analysis/                 # Daily AI analysis results
│   │   ├── _deep_analysis_cache/     # Phase 2 source context cache (AST-based)
│   │   ├── context/
│   │   │   ├── architecture.json
│   │   │   └── arch_deltas.json
│   │   ├── index.json
│   │   └── commits-index.json
│   └── vllm-ascend/                  # vllm-ascend project data
│       ├── meta.json
│       ├── commits/
│       ├── analysis/
│       ├── lessons/                  # Adaptation lessons (main2main experience)
│       ├── context/
│       │   ├── architecture.json
│       │   └── arch_deltas.json
│       ├── index.json
│       ├── commits-index.json
│       └── adaptation-status.json
├── daily_refresh.sh                  # Local one-shot full pipeline script
├── src/
│   ├── __init__.py
│   ├── mcp_server_app.py             # MCP Server (stdio-based, 28 tools)
│   ├── data/
│   │   ├── __init__.py
│   │   ├── _opencode_client.py       # opencode CLI wrapper
│   │   ├── _extract_patches.py       # Patch catalog extractor (deterministic)
│   │   ├── _source_repo.py           # Local repo discovery/pull (internal)
│   │   ├── _source_cache.py          # AST-based source context cache (internal)
│   │   ├── _track_arch_delta.py      # Architecture delta chain (internal)
│   │   ├── fetch_commits.py          # Fetch commit data
│   │   ├── analyze_commits.py        # Two-phase AI analysis
│   │   ├── deep_analyze_commits.py   # Phase 2 deep analysis via opencode
│   │   ├── generate_context.py       # Architecture context generation
│   │   ├── build_index.py            # Search index builder
│   │   └── track_adaptation.py       # Adaptation status CLI
├── serve.py                          # Dev server for dashboard
├── site/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── tests/
│   ├── test_arch_delta.py
│   └── test_core.py
├── docs/
│   ├── data-spec.md                  # Data format specification
│   ├── mcp-usage-guide.md            # AI agent usage guide
│   ├── sop.md                        # Standard operating procedures
│   └── todo.md                       # Backlog / roadmap notes
└── .gitignore
```

## Quick Start

### 1. Fetch Commits

`fetch_commits.py` requires a local source repo and a target date:

```bash
# Fetch commits for a specific date (Beijing time) from a local repo
python src/data/fetch_commits.py --repo vllm-project/vllm --local-repo ~/code/vllm --date 2026-07-30
python src/data/fetch_commits.py --repo vllm-project/vllm-ascend --local-repo ~/code/vllm-ascend --date 2026-07-30

# Overwrite existing data for a date
python src/data/fetch_commits.py --repo vllm-project/vllm --local-repo ~/code/vllm --date 2026-07-30 --force
```

> Different from earlier versions: `--local-repo` and `--date` are now required. The script reads commits locally rather than calling the GitHub API, so there is no `--refresh-date` or token-only mode.

### 2. Set Up API Keys & Environment Variables

`analyze_commits.py` Phase 1 calls an OpenAI-compatible `/chat/completions` endpoint, configured via environment variables:

```bash
# Required: DeepSeek / compatible API key for Phase 1 bulk analysis
export LLM_API_KEY="sk-your-deepseek-key"

# Optional: override the API base URL (default: Volcengine ARK coding endpoint)
# export LLM_API_BASE="https://ark.cn-beijing.volces.com/api/coding/v3"

# Optional: override the model id (default: deepseek-v4-flash)
# export LLM_MODEL="deepseek-v4-flash"
```

The opencode-driven steps (Phase 2 deep analysis, `generate_context.py`) are configured via `OPENCODE_*` / provider config:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENCODE_AUTH_TOKEN` | — | API key for the opencode provider (used by `opencode.json` / CI) |
| `OPENCODE_MODEL` | (empty → use `opencode.json` default) | Provider/model selector passed to `opencode run -m`, e.g. `volcengine-plan/ark-code-latest` |
| `OPENCODE_BIN` | discovered via `PATH` | Path to the `opencode` binary |
| `OPENCODE_TIMEOUT` | `600` | Per-call timeout (seconds) for opencode |

Optionally, `GH_TOKEN` or `GITHUB_TOKEN` is read by the MCP server when persisting adaptation lessons (`submit_lesson`) and by the `get_commit_diff` GitHub API fallback.

### 3. Generate Architecture Context

```bash
# Generate for vllm (uses opencode agent to read source code)
python src/data/generate_context.py --repo vllm-project/vllm --force

# Generate for vllm-ascend
python src/data/generate_context.py --repo vllm-project/vllm-ascend --force

# Cross-reference vllm vs vllm-ascend
python src/data/generate_context.py --cross-reference --force

# Both repos at once with cross-reference
python src/data/generate_context.py \
  --repo vllm-project/vllm \
  --repo vllm-project/vllm-ascend \
  --cross-reference --force
```

### 4. Run AI Analysis

```bash
# Analyze all unanalyzed dates (catch-up mode, default)
python src/data/analyze_commits.py --repo vllm-project/vllm

# Analyze a specific date
python src/data/analyze_commits.py --repo vllm-project/vllm --date 2024-01-15 --force

# Analyze only the latest date
python src/data/analyze_commits.py --repo vllm-project/vllm --latest

# Explicit catch-up mode (same as default)
python src/data/analyze_commits.py --repo vllm-project/vllm --catch-up
```

The analysis runs in two phases:
1. **Phase 1**: DeepSeek batch analysis for all commits. Includes path-based triage — commits modifying only tests/docs/CI/platform-specific code are auto-skipped (no LLM cost), using the `not_used_by_ascend` path list from architecture.json (refreshed on-demand).
2. **Phase 2**: opencode agent deep analysis for ascend_affected commits (identifies specific interfaces, adaptation effort, and guide).

### 5. Build Search Index

```bash
python src/data/build_index.py --ascend-repo-path ~/code/vllm-ascend
```

### 6. Track Adaptation Status

```bash
# Initialize tracking (scans analysis for ascend_affected commits, auto-classifies)
python src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend

# Force re-initialize with a specific start date
python src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend \
  --since 2026-07-15 --force

# Show progress (only two states: pending / adapted)
python src/data/track_adaptation.py status

# List commits by status
python src/data/track_adaptation.py list --status pending
```

The `init` subcommand also accepts `--data-dir` (default `data`) and `--local-repo` (vllm source path, used as a git-log fallback when commits are missing the diff needed to classify).

### One-Shot Local Pipeline

To run the whole fetch → analyze → deep analyze → index → track → clean pipeline locally in one shot (mirroring `daily-commit.yml`), use `daily_refresh.sh`:

```bash
# Analyze yesterday's commits for both repos
LLM_API_KEY=sk-xxx ./daily_refresh.sh

# Specific date + custom repo paths
LLM_API_KEY=sk-xxx ./daily_refresh.sh --date 2026-07-27 \
  --vllm-path ~/code/vllm --ascend-path ~/code/vllm-ascend

# Only vllm, skip deep analysis
LLM_API_KEY=sk-xxx ./daily_refresh.sh --repo vllm --skip-deep-analyze

# See all options
./daily_refresh.sh --help
```

Available flags: `--repo`, `--date`, `--vllm-path`, `--ascend-path`, `--data-dir`, `--force`, `--skip-fetch`, `--skip-analyze`, `--skip-deep-analyze`, `--skip-build-index`, `--skip-track-adaptation`, `--skip-clean`, `--skip-pull`.

Every flag also has a matching environment variable (flags take precedence):

| Env var | Corresponding flag | Default |
|---------|--------------------|---------|
| `DATA_DIR` | `--data-dir` | `./data` |
| `REPOS_DIR` | (repo root) | `./repos` |
| `VLLM_REPO_PATH` | `--vllm-path` | `$REPOS_DIR/vllm` |
| `ASCEND_REPO_PATH` | `--ascend-path` | `$REPOS_DIR/vllm-ascend` |
| `DATE` | `--date` | yesterday (Beijing time) |
| `FORCE` | `--force` | `false` |
| `REPO` | `--repo` | (all repos) |
| `LLM_API_KEY` | — (Phase 1) | required |

Set `SKIP_PULL` to `true` (or pass `--skip-pull`) to avoid `git pull` on local repos.

### 7. Start MCP Server (for AI Agents)

```bash
python -m src.mcp_server_app \
  --data-dir /path/to/vllm-report/data \
  --ascend-repo-path /path/to/vllm-ascend
```

Configure in your AI tool:

**OpenCode** (in `opencode.json` or `opencode.jsonc`):
```jsonc
{
  "mcp": {
    "vllm-report": {
      "type": "local",
      "command": ["python", "-m", "src.mcp_server_app", "--data-dir", "/path/to/vllm-report/data", "--ascend-repo-path", "/path/to/vllm-ascend"],
      "enabled": true
    }
  }
}
```

Or via CLI:
```bash
opencode mcp add vllm-report -- python -m src.mcp_server_app \
    --data-dir /path/to/vllm-report/data \
    --ascend-repo-path /path/to/vllm-ascend
```

### 8. View Dashboard

```bash
python serve.py
# or open site/index.html directly
```

## AI Agent Integration

See [docs/mcp-usage-guide.md](docs/mcp-usage-guide.md) for detailed usage scenarios:

- **OpenCode** (native MCP): `opencode mcp add vllm-report -- python -m src.mcp_server_app ...` or configure `mcp` in `opencode.json` / `opencode.jsonc`
- **Other tools**: Wrap MCP Server with `socat` for HTTP access, or read JSON files directly from `data/`

## GitHub Actions Setup

### Required Secrets

| Secret | Description |
|--------|-------------|
| `DEEPSEEK_API_KEY` | API key for DeepSeek (Phase 1 bulk analysis) |
| `OPENCODE_AUTH_TOKEN` | OpenAI-compatible API key used by opencode (Phase 2 deep analysis + architecture generation) |
| `GH_TOKEN` (or `GITHUB_TOKEN`) | Optional — GitHub token used by the MCP server to push recorded lessons and by `get_commit_diff` for the GitHub API fallback |

> The opencode model is configured in a `~/.config/opencode/opencode.json` block written by the workflows, or overridden via the `OPENCODE_MODEL` env var. The current default is `deepseek/deepseek-v4-flash` pointed at the Volcengine ARK base URL (`https://ark.cn-beijing.volces.com/api/coding/v3`, model `deepseek-chat`). To change providers, edit the opencode config JSON block or set `OPENCODE_MODEL`.

### Workflows

- **`daily-commit.yml`** — Runs daily at 02:00 CST (or via manual `workflow_dispatch` with an optional date input): fetch → Phase 1 DeepSeek analysis → Phase 2 opencode analysis → build index → track adaptation → clean stale data → deploy. Runs for both vllm and vllm-ascend, checking out their sources into `repos/`.
- **`refresh-context.yml`** — Run on-demand (`workflow_dispatch`): checkout vllm/vllm-ascend source → generate architecture via opencode → cross-reference → rebuild index → track adaptation. (This replaced the old scheduled `weekly-context.yml`.)
- **`pages.yml`** — Deploys GitHub Pages on push to `site/` or `data/`

## Data Format

See [docs/data-spec.md](docs/data-spec.md) for complete data format specification.

### Key Data Files

| File | Description |
|------|-------------|
| `data/README.json` | Entry point for AI agents |
| `data/{repo}/commits/{date}.json` | Raw commit data |
| `data/{repo}/analysis/{date}.json` | AI analysis results (with `architecture_based_on_sha`, `architecture_impact`, optional `deep_analysis`) |
| `data/{repo}/_deep_analysis_cache/` | Phase 2 source-context cache (AST-extracted interfaces per commit) |
| `data/{repo}/context/architecture.json` | Architecture knowledge (with `knowledge_base`, `cross_project_relationship`, `architecture_history`) |
| `data/{repo}/context/arch_deltas.json` | Architecture delta chain between commits |
| `data/{repo}/index.json` | Lightweight search index (tags/modules/keywords → SHA list) |
| `data/{repo}/commits-index.json` | SHA → {date, message} lookup table |
| `data/vllm/` | vllm project data |
| `data/vllm-ascend/` | vllm-ascend project data |
| `data/vllm-ascend/adaptation-status.json` | Adaptation progress tracking |
| `data/vllm-ascend/lessons/` | Adaptation lessons recorded from main2main runs |

## Architecture Knowledge

The architecture knowledge is stored in `data/{repo}/context/architecture.json`, (re)generated on demand via the `refresh-context.yml` workflow or locally with `generate_context.py`, in both cases using the opencode agent to read actual source code. It covers **12 dimensions** across two files (~70KB for vllm, ~90KB for vllm-ascend).

### Dimensions

| Dimension | Size | Description | Who Maintains |
|-----------|------|-------------|---------------|
| `overview` | ~0.2KB | Project overview — what it is, what problem it solves | opencode (on-demand) |
| `modules` | ~6.5KB | Core module list (name, path, key_classes, description) — 23 modules for vllm, 25 for vllm-ascend | opencode (on-demand) |
| `key_abstractions` | ~12KB | Key classes/interfaces with inheritance (`inherits_from`), key method signatures, ascend implementations | opencode (on-demand) |
| `implementation_principles` | ~5KB | Implementation principles — how core workflows work (scheduler loop, attention backend selection, platform plugin loading, etc.) | opencode (on-demand) |
| `module_dependencies` | ~0.3KB | Text description of inter-module dependency graph | opencode (on-demand) |
| `hardware_abstraction` | ~1.3KB | Hardware abstraction layer — platform-independent interfaces vs platform-specific implementations | opencode (on-demand) |
| `interface_surface` | ~7.7KB | Interface surface — `inheritable_interfaces` (8 interfaces that ascend inherits/overrides) + `not_used_by_ascend` (63 paths that are platform-specific and can be skipped) | opencode (on-demand) |
| `test_structure` | ~0.1KB | Test structure overview | opencode (on-demand) |
| `cross_project_relationship` | ~13KB | Cross-project mapping — `patch_impact_map` (24 vllm patched modules → ascend patch files), `impact_judgment_rules` (definitely/potentially/never_affected_paths), `vllm_to_ascend_map` | opencode (on-demand, cross-reference) |
| `knowledge_base` | ~3KB (vllm) / ~21KB (vllm-ascend) | Knowledge base — see below for sub-fields | Mixed |
| `architecture_history` | varies | History of past architecture snapshots (used to compute per-commit architecture deltas) | Auto-generated (accumulated on refresh) |
| `repo` / `generated_at` / `commit_sha` | — | Metadata: which repo, when generated, which commit it's based on | Auto-generated |

### knowledge_base sub-fields

The `knowledge_base` field contains three sub-fields:

| Sub-field | Size | Description | Update Method |
|-----------|------|-------------|---------------|
| `patch_catalog` | ~17.6KB (ascend) | All vllm-ascend patches organized by category: `platform_patches` (21 patches), `worker_patches` (24 patches), `v2_worker_patches` (9 patches). Each patch entry includes `targets` (modified classes/functions), `why`, `how`, `related_pr`, `future_plan`. | `_extract_patches.py` — deterministic regex parsing of `patch/__init__.py`, no LLM cost |
| `development_workflows` | ~2.1KB | Fixed workflow templates for common development tasks: adding a new model, adding a config item, adding a platform backend (vllm); adding platform patch, worker patch, new model, env variable, attention backend (vllm-ascend). | Hardcoded in `generate_context.py` |
| `testing_guide` | ~0.6KB | Test commands and environment setup for both vllm and vllm-ascend: unit test commands, e2e test commands, lint commands, environment setup instructions. | Hardcoded in `generate_context.py` |

### cross_project_relationship sub-fields

| Sub-field | Description |
|-----------|-------------|
| `patch_impact_map` | Maps vllm patched modules (e.g., `vllm/v1/engine/core.py`) to corresponding vllm-ascend patch files (e.g., `patch_balance_schedule.py`). 24 entries. |
| `impact_judgment_rules` | Three categories of path matching rules: `definitely_affected_paths` (changes that always affect ascend), `never_affected_paths` (pure CUDA/ROCm/FlashInfer code), `potentially_affected_paths` (changes that need human review). |
| `vllm_to_ascend_map` | Maps vllm modules/classes to their ascend counterparts. |
| `ascend_only_components` | Components that exist only in vllm-ascend (no upstream equivalent). |

### Dynamic Maintenance

- **`interface_surface.not_used_by_ascend`** — Regenerated by opencode on every architecture refresh. Reflects current codebase state: paths that are pure CUDA/ROCm/FlashInfer platform-specific code that ascend never touches.
- **`patch_catalog`** — Updated by `_extract_patches.py` every time `patch/__init__.py` changes. No LLM call needed.
- **`development_workflows` and `testing_guide`** — Hardcoded templates that rarely change.

### Progressive Loading via MCP

See [docs/mcp-usage-guide.md](docs/mcp-usage-guide.md) for progressive loading patterns and usage scenarios for all 28 MCP tools.

## MCP Server Tools

The MCP server (`src/mcp_server_app.py`) provides **28 tools** across four categories plus an **experience/knowledge** category:

**Architecture Analysis (13):**
- `get_architecture_overview` — **[Progressive]** Return overview + modules list, use as first call
- `get_module_info` — **[Progressive]** Return detailed info for a single module (fuzzy match)
- `get_interface_surface` — **[Progressive]** Return interface surface (inheritable interfaces + not_used_by_ascend)
- `get_key_abstractions` — **[Progressive]** Return key classes with inheritance info
- `get_implementation_principles` — **[Progressive]** Return implementation principles
- `get_hardware_abstraction` — **[Progressive]** Return hardware abstraction layer info
- `get_development_workflows` — **[Progressive]** Return development workflow templates
- `get_testing_guide` — **[Progressive]** Return testing guide
- `get_architecture_context` — **[Full]** Return complete architecture.json
- `get_architecture_at_commit` — Return architecture snapshot at a specific commit
- `get_architecture_diff` — Diff architecture between two commits
- `get_architecture_freshness` — Check if architecture.json is up-to-date
- `get_commit_arch_delta` — Architecture delta for a single commit

**Adaptation Management (5):**
- `get_adaptation_baseline` — Return current vllm baseline verified by vllm-ascend
- `advance_baseline` — Advance the vllm baseline (auto-marks covered commits as adapted)
- `get_pending_adaptations` — Get list of pending adaptation commits
- `get_adaptation_guide` — Get adaptation guide for a commit (markdown format)
- `get_adaptation_roadmap` — Full adaptation roadmap from sha_from to sha_to

**Change Analysis (6):**
- `get_ascend_impact_summary` — Summary of commits that affect vllm-ascend
- `get_commit_diff` — Get full diff for a commit (local data first, GitHub API fallback)
- `get_commit_impact_batch` — Batch query ascend-impact analysis for a list of vllm commits (structured JSON, used for deterministic routing)
- `search_analysis` — Cross-date search with keyword/tag/date filters
- `get_daily_analysis` — Return analysis data for a specific date
- `get_module_history` — Get change history for a specific module

**Engineering Support (2):**
- `get_cross_project_mapping` — Return vllm ↔ vllm-ascend cross-project mapping
- `get_patch_catalog` — Return vllm-ascend patch catalog (optionally filtered by platform/worker category)

**Experience / Lessons (2):** — practical adaptation knowledge auto-recorded from main2main runs (recurring E2E fix failures, new patterns)
- `get_adaptation_lessons` — Retrieve recorded lessons by keywords/tags (symptom, root_cause, fix_guidance). **Call this before re-diagnosing an E2E failure** — a matching lesson fixes it in one pass.
- `submit_lesson` — Record a lesson (used by main2main_flow automatically when a step needed E2E fix rounds; also callable by agents to persist new findings)

See [docs/mcp-usage-guide.md](docs/mcp-usage-guide.md) for usage scenarios.

## License

Apache 2.0