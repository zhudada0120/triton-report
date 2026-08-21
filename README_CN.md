# vllm-report

[vllm](https://github.com/vllm-project/vllm) 和 [vllm-ascend](https://github.com/vllm-project/vllm-ascend) 的每日 commit 监控与 AI 分析系统。提供知识库供 AI Agent（OpenCode 等）使用，支持 vllm-ascend 代码升级和 main2main 适配。

## 功能

- **每日 Commit 抓取** — 每天北京时间 02:00 自动通过 GitHub Actions 抓取新 commit（含完整 diff）
- **双阶段 AI 分析** —
  - **Phase 1**：DeepSeek API 批量分析（意图、风险、ascend 影响、测试影响），附带路径预筛选（path-based triage）自动跳过非 ascend 相关 commit（tests/docs/CI/平台特化代码），降低 LLM API 成本
  - **Phase 2**：opencode Agent 深度分析 ascend_affected 的 commit——通过读取实际源码识别具体影响接口、适配工作量和适配指南（使用 AST 提取的源码上下文缓存）
- **Diff 感知的架构影响标记** — 修改关键接口文件的 commit 自动获得 `architecture_impact` 标记（affected_interfaces、recommend_refresh），根据跨项目关系规则的文件路径匹配检测
- **架构上下文缓存** — 按需（`refresh-context.yml` 工作流）通过 opencode Agent 自动生成项目架构摘要，注入 AI 分析 prompt 以提高影响判断的准确性
- **知识库** — architecture.json 的 `knowledge_base` 字段包含 patch 目录、开发工作流、测试指南
- **Patch 目录提取** — 通过 `_extract_patches.py` 对 vllm-ascend 的 `patch/__init__.py` 进行确定性正则解析，生成结构化 JSON，无需 LLM 调用
- **源码上下文缓存** — 深分析前通过 AST 从源文件提取公开接口，Phase 2 无需额外 LLM 调用即可了解文件结构
- **检索索引** — 关键词/标签/模块索引，支持跨日期快速搜索
- **适配状态跟踪** — 自动跟踪哪些 vllm 上游 commit 需要适配，仅维护两种状态：`pending`（待适配）和 `adapted`（已被基线覆盖）。每次 fetch+analyze 后自动重新生成。
- **适配经验库** — 从 main2main 运行中自动沉淀的实战适配知识（重复出现的 E2E 修复失败、新适配模式），AI Agent 可查询并按经验一步修复
- **MCP Server** — 28 个工具的 stdio 模式 MCP Server，供 AI Agent 查询知识库。支持渐进式加载——Agent 先获取轻量概览，再按需深入具体模块
- **静态 Web Dashboard** — 深色主题监控页面，包含 commit 列表、diff 查看器、AI 分析覆盖、适配状态筛选
- **数据生命周期管理** — 过期数据清理删除无对应分析的 commit 数据

## 项目结构

```
vllm-report/
├── .github/
│   ├── workflows/
│   │   ├── daily-commit.yml          # 每日抓取 + AI 分析
│   │   ├── refresh-context.yml       # 按需（重新）生成架构上下文
│   │   └── pages.yml                 # GitHub Pages 部署
│   └── scripts/
│       └── clean_stale_data.py       # 数据清理（CI 专用）
├── data/
│   ├── README.json                   # AI Agent 入口引导文件
│   ├── vllm/                         # vllm 项目数据
│   │   ├── meta.json
│   │   ├── commits/                  # 每日 commit JSON 文件
│   │   ├── analysis/                 # 每日 AI 分析结果
│   │   ├── _deep_analysis_cache/     # Phase 2 源码上下文缓存（AST 提取）
│   │   ├── context/
│   │   │   ├── architecture.json
│   │   │   └── arch_deltas.json
│   │   ├── index.json
│   │   ├── commits-index.json
│   └── vllm-ascend/                  # vllm-ascend 项目数据
│       ├── meta.json
│       ├── commits/
│       ├── analysis/
│       ├── lessons/                  # 适配经验（main2main 实战沉淀）
│       ├── context/
│       │   ├── architecture.json
│       │   └── arch_deltas.json
│       ├── index.json
│       ├── commits-index.json
│       └── adaptation-status.json
├── daily_refresh.sh                  # 本地一键全流程脚本
├── src/
│   ├── __init__.py
│   ├── mcp_server_app.py             # MCP Server（stdio 模式，28 个工具）
│   ├── data/
│   │   ├── __init__.py
│   │   ├── _opencode_client.py       # opencode CLI 封装
│   │   ├── _extract_patches.py       # Patch 目录提取器（确定性解析）
│   │   ├── _source_repo.py           # 仓库发现/拉取（内部模块）
│   │   ├── _source_cache.py          # AST 源码上下文缓存（内部模块）
│   │   ├── _track_arch_delta.py      # 架构增量链（内部模块）
│   │   ├── fetch_commits.py          # 抓取 commit 数据
│   │   ├── analyze_commits.py        # 双阶段 AI 分析
│   │   ├── deep_analyze_commits.py   # Phase 2 opencode 深度分析
│   │   ├── generate_context.py       # 架构上下文生成
│   │   ├── build_index.py            # 检索索引构建器
│   │   └── track_adaptation.py       # 适配状态 CLI 工具
├── serve.py                          # Dashboard 开发服务器
├── site/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── tests/
│   ├── test_arch_delta.py
│   └── test_core.py
├── docs/
│   ├── data-spec.md                  # 数据格式规范
│   ├── mcp-usage-guide.md            # AI Agent 使用指南
│   ├── sop.md                        # 标准操作流程
│   └── todo.md                       # 待办/路线图
└── .gitignore
```

## 快速开始

### 1. 抓取 Commit

`fetch_commits.py` 需要本地源码仓库和目标日期（北京时间）：

```bash
# 从本地仓库抓取指定日期的 commit（北京时间）
python src/data/fetch_commits.py --repo vllm-project/vllm --local-repo ~/code/vllm --date 2026-07-30
python src/data/fetch_commits.py --repo vllm-project/vllm-ascend --local-repo ~/code/vllm-ascend --date 2026-07-30

# 覆盖现有数据强制刷新
python src/data/fetch_commits.py --repo vllm-project/vllm --local-repo ~/code/vllm --date 2026-07-30 --force
```

> 与旧版本不同：`--local-repo` 和 `--date` 现在为必填。脚本在本地读取 commit 而非调用 GitHub API，因此不再有 `--refresh-date` 或纯 token 模式。

### 2. 设置 API Key 与环境变量

`analyze_commits.py` 的 Phase 1 会调用 OpenAI 兼容的 `/chat/completions` 接口，通过环境变量配置：

```bash
# 必填：DeepSeek / 兼容 API Key，用于 Phase 1 批量分析
export LLM_API_KEY="sk-your-deepseek-key"

# 可选：覆盖 API base URL（默认：火山方舟 ARK coding 端点）
# export LLM_API_BASE="https://ark.cn-beijing.volces.com/api/coding/v3"

# 可选：覆盖模型名（默认：deepseek-v4-flash）
# export LLM_MODEL="deepseek-v4-flash"
```

opencode 相关步骤（Phase 2 深分析、`generate_context.py`）通过 `OPENCODE_*` 环境变量配置：

| 变量 | 默认 | 用途 |
|------|------|------|
| `OPENCODE_AUTH_TOKEN` | — | opencode 提供方 API Key（供 `opencode.json` / CI 使用） |
| `OPENCODE_MODEL` | （空 → 用 `opencode.json` 默认） | 传给 `opencode run -m` 的 provider/model 选择器，如 `volcengine-plan/ark-code-latest` |
| `OPENCODE_BIN` | 从 `PATH` 发现 | `opencode` 可执行文件路径 |
| `OPENCODE_TIMEOUT` | `600` | opencode 单次调用超时（秒） |

另外，MCP Server 的 `submit_lesson` 持久化适配经验、以及 `get_commit_diff` 的 GitHub API 回退时，会读取 `GH_TOKEN` 或 `GITHUB_TOKEN`。

### 3. 生成架构上下文

```bash
# 为 vllm 生成（使用 opencode Agent 读取源码）
python src/data/generate_context.py --repo vllm-project/vllm --force

# 为 vllm-ascend 生成
python src/data/generate_context.py --repo vllm-project/vllm-ascend --force

# 交叉分析 vllm 和 vllm-ascend
python src/data/generate_context.py --cross-reference --force

# 一次完成两个仓库和交叉分析
python src/data/generate_context.py \
  --repo vllm-project/vllm \
  --repo vllm-project/vllm-ascend \
  --cross-reference --force
```

### 4. 运行 AI 分析

```bash
# 分析所有未分析日期（catch-up 模式，默认行为）
python src/data/analyze_commits.py --repo vllm-project/vllm

# 分析指定日期
python src/data/analyze_commits.py --repo vllm-project/vllm --date 2024-01-15 --force

# 仅分析最新日期
python src/data/analyze_commits.py --repo vllm-project/vllm --latest

# 显式 catch-up 模式（与默认行为相同）
python src/data/analyze_commits.py --repo vllm-project/vllm --catch-up
```

分析分两阶段：
1. **Phase 1**：DeepSeek 批量分析所有 commit。包含路径预筛选（path-based triage）——仅修改 tests/docs/CI/平台特化代码的 commit 自动跳过（无 LLM 成本），使用 architecture.json 中的 `not_used_by_ascend` 路径列表（按需刷新）
2. **Phase 2**：opencode Agent 深度分析 ascend_affected 的 commit（识别具体影响接口、适配工作量、适配指南）

### 5. 构建检索索引

```bash
python src/data/build_index.py --ascend-repo-path ~/code/vllm-ascend
```

### 6. 跟踪适配状态

```bash
# 初始化跟踪（扫描分析结果，自动按基线划分 adapted/pending）
python src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend

# 强制重新初始化，指定起始日期
python src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend \
  --since 2026-07-15 --force

# 查看进度（仅两种状态：pending / adapted）
python src/data/track_adaptation.py status

# 按状态列出 commit
python src/data/track_adaptation.py list --status pending
```

`init` 子命令还支持 `--data-dir`（默认 `data`）和 `--local-repo`（vllm 源码路径，用于缺少判断所需 diff 时的 git log 回退）。

### 本地一键流水线

如需在本地一次性跑完 抓取 → 分析 → 深分析 → 建索引 → 跟踪 → 清理（对应 `daily-commit.yml`），使用 `daily_refresh.sh`：

```bash
# 分析两个仓库昨天的 commit
LLM_API_KEY=sk-xxx ./daily_refresh.sh

# 指定日期和本地仓库路径
LLM_API_KEY=sk-xxx ./daily_refresh.sh --date 2026-07-27 \
  --vllm-path ~/code/vllm --ascend-path ~/code/vllm-ascend

# 只处理 vllm，跳过深度分析
LLM_API_KEY=sk-xxx ./daily_refresh.sh --repo vllm --skip-deep-analyze

# 查看全部选项
./daily_refresh.sh --help
```

可用参数：`--repo`、`--date`、`--vllm-path`、`--ascend-path`、`--data-dir`、`--force`、`--skip-fetch`、`--skip-analyze`、`--skip-deep-analyze`、`--skip-build-index`、`--skip-track-adaptation`、`--skip-clean`、`--skip-pull`。

每个参数都有对应的环境变量（参数优先于环境变量）：

| 环境变量 | 对应参数 | 默认 |
|----------|---------|------|
| `DATA_DIR` | `--data-dir` | `./data` |
| `REPOS_DIR` | （仓库根目录） | `./repos` |
| `VLLM_REPO_PATH` | `--vllm-path` | `$REPOS_DIR/vllm` |
| `ASCEND_REPO_PATH` | `--ascend-path` | `$REPOS_DIR/vllm-ascend` |
| `DATE` | `--date` | 昨天（北京时间） |
| `FORCE` | `--force` | `false` |
| `REPO` | `--repo` | （全部仓库） |
| `LLM_API_KEY` | —（Phase 1） | 必填 |

设 `SKIP_PULL=true`（或传 `--skip-pull`）可跳过对本地仓库的 `git pull`。

### 7. 启动 MCP Server（供 AI Agent 使用）

```bash
python -m src.mcp_server_app \
  --data-dir /path/to/vllm-report/data \
  --ascend-repo-path /path/to/vllm-ascend
```

在 AI 工具中配置：

**OpenCode**（`opencode.json` 或 `opencode.jsonc`）：
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

或用命令添加：
```bash
opencode mcp add vllm-report -- python -m src.mcp_server_app \
    --data-dir /path/to/vllm-report/data \
    --ascend-repo-path /path/to/vllm-ascend
```

### 8. 查看 Dashboard

```bash
python serve.py
# 或直接打开 site/index.html
```

## AI Agent 集成

详见 [docs/mcp-usage-guide.md](docs/mcp-usage-guide.md) 的使用场景说明：

- **OpenCode**（原生 MCP）：`opencode mcp add vllm-report -- python -m src.mcp_server_app ...` 或在 `opencode.json` / `opencode.jsonc` 中配置 `mcp`
- **其他工具**：通过 `socat` 包装为 HTTP 访问，或直接从 `data/` 目录读取 JSON 文件

## GitHub Actions 配置

### 必须的 Secrets

| Secret | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（Phase 1 批量分析） |
| `OPENCODE_AUTH_TOKEN` | opencode 使用的 OpenAI 兼容 API Key（Phase 2 深度分析 + 架构生成） |
| `GH_TOKEN`（或 `GITHUB_TOKEN`） | 可选 — MCP Server 推送存档经验、以及 `get_commit_diff` 的 GitHub API 回退时使用的 token |

> opencode 模型在工作流写入的 `~/.config/opencode/opencode.json` 中配置，也可用 `OPENCODE_MODEL` 环境变量覆盖。目前默认使用 `deepseek/deepseek-v4-flash`，base URL 指向火山方舟 ARK（`https://ark.cn-beijing.volces.com/api/coding/v3`，模型 `deepseek-chat`）。如需切换服务商，修改工作流里的 opencode 配置或设置 `OPENCODE_MODEL`。

### 工作流

- **`daily-commit.yml`** — 每天 02:00 CST 运行（或手动 `workflow_dispatch` 指定日期）：抓取 → Phase 1 DeepSeek 分析 → Phase 2 opencode 分析 → 构建索引 → 跟踪适配 → 清理过期数据 → 部署。同时处理 vllm 和 vllm-ascend，将源码检出到 `repos/`。
- **`refresh-context.yml`** — 按需（`workflow_dispatch`）：检出 vllm/vllm-ascend 源码 → 通过 opencode 生成架构 → 交叉分析 → 重建索引 → 跟踪适配。（取代了原定时 `weekly-context.yml`。）
- **`pages.yml`** — 推送 `site/` 或 `data/` 时部署 GitHub Pages

## 数据格式

详见 [docs/data-spec.md](docs/data-spec.md) 的完整数据格式规范。

### 关键数据文件

| 文件 | 说明 |
|------|------|
| `data/README.json` | AI Agent 入口引导文件 |
| `data/{repo}/commits/{date}.json` | 原始 commit 数据 |
| `data/{repo}/analysis/{date}.json` | AI 分析结果（含 `architecture_based_on_sha`、`architecture_impact`、可选 `deep_analysis`） |
| `data/{repo}/_deep_analysis_cache/` | Phase 2 源码上下文缓存（按 commit AST 提取的接口） |
| `data/{repo}/context/architecture.json` | 架构知识库（含 `knowledge_base`、`cross_project_relationship`、`architecture_history`） |
| `data/{repo}/context/arch_deltas.json` | commit 间的架构增量链 |
| `data/{repo}/index.json` | 轻量检索索引（标签/模块/关键词 → SHA 列表） |
| `data/{repo}/commits-index.json` | SHA → {date, message} 查找表 |
| `data/vllm/` | vllm 项目数据 |
| `data/vllm-ascend/` | vllm-ascend 项目数据 |
| `data/vllm-ascend/adaptation-status.json` | 适配进度跟踪 |
| `data/vllm-ascend/lessons/` | main2main 运行中沉淀的适配经验 |

## 架构知识库

架构知识存储在 `data/{repo}/context/architecture.json` 中，按需（`refresh-context.yml` 工作流或本地 `generate_context.py`）由 opencode Agent 通过读取实际源码生成。覆盖 **12 个维度**，两个文件分别约 70KB（vllm）和 90KB（vllm-ascend）。

### 知识维度

| 维度 | 大小 | 说明 | 维护方式 |
|------|------|------|---------|
| `overview` | ~0.2KB | 项目概述——是什么、解决什么问题 | opencode（按需） |
| `modules` | ~6.5KB | 核心模块列表（名称、路径、关键类、描述）——vllm 23 个模块，vllm-ascend 25 个 | opencode（按需） |
| `key_abstractions` | ~12KB | 关键抽象/类，含继承关系（`inherits_from`）、关键方法签名、ascend 实现类名 | opencode（按需） |
| `implementation_principles` | ~5KB | 实现原理——核心工作流（调度循环、attention 后端选择、平台插件加载等） | opencode（按需） |
| `module_dependencies` | ~0.3KB | 模块间依赖关系文字描述 | opencode（按需） |
| `hardware_abstraction` | ~1.3KB | 硬件适配层——平台无关接口 vs 平台特有实现 | opencode（按需） |
| `interface_surface` | ~7.7KB | 接口面——`inheritable_interfaces`（8 个 ascend 继承/复写的接口）+ `not_used_by_ascend`（63 个可跳过的平台特化路径） | opencode（按需） |
| `test_structure` | ~0.1KB | 测试结构概览 | opencode（按需） |
| `cross_project_relationship` | ~13KB | 跨项目映射——`patch_impact_map`（24 个 vllm 被 patch 模块→ascend patch 文件）、`impact_judgment_rules`（必然/可能/绝不影响路径）、`vllm_to_ascend_map` | opencode（按需，交叉分析） |
| `knowledge_base` | ~3KB (vllm) / ~21KB (vllm-ascend) | 知识库——见下方子字段说明 | 混合 |
| `architecture_history` | 不定 | 历史架构快照列表（用于计算各 commit 的架构增量） | 自动生成（刷新时累积） |
| `repo` / `generated_at` / `commit_sha` | — | 元信息：所属仓库、生成时间、基于的 commit | 自动生成 |

### knowledge_base 子字段

| 子字段 | 大小 | 说明 | 更新方式 |
|--------|------|------|---------|
| `patch_catalog` | ~17.6KB (ascend) | 所有 vllm-ascend patch，按类别组织：`platform_patches`（21 个）、`worker_patches`（24 个）、`v2_worker_patches`（9 个）。每个 patch 含 `targets`（修改的类/函数）、`why`、`how`、`related_pr`、`future_plan` | `_extract_patches.py` 对 `patch/__init__.py` 进行确定性正则解析，无 LLM 成本 |
| `development_workflows` | ~2.1KB | 固定开发工作流模板：添加模型、配置项、平台后端（vllm）；添加 platform patch、worker patch、新模型适配、环境变量、attention 后端（vllm-ascend） | 硬编码在 `generate_context.py` 中 |
| `testing_guide` | ~0.6KB | 测试命令和环境设置：单元测试、端到端测试、lint 命令、环境准备步骤 | 硬编码在 `generate_context.py` 中 |

### cross_project_relationship 子字段

| 子字段 | 说明 |
|--------|------|
| `patch_impact_map` | vllm 被 patch 模块（如 `vllm/v1/engine/core.py`）到对应 vllm-ascend patch 文件（如 `patch_balance_schedule.py`）的映射。共 24 条。 |
| `impact_judgment_rules` | 三类路径匹配规则：`definitely_affected_paths`（修改必然影响 ascend）、`never_affected_paths`（纯 CUDA/ROCm/FlashInfer 代码）、`potentially_affected_paths`（需要人工判断） |
| `vllm_to_ascend_map` | vllm 模块/类到 ascend 对应实现的映射 |
| `ascend_only_components` | 仅 vllm-ascend 特有的组件（上游无对应） |

### 动态维护机制

- **`interface_surface.not_used_by_ascend`** — 每次架构刷新时由 opencode 自动更新，始终反映当前代码库状态：哪些路径是纯 CUDA/ROCm/FlashInfer 平台特化代码、ascend 绝不会触及
- **`patch_catalog`** — 每次 `patch/__init__.py` 变更时由 `_extract_patches.py` 自动更新，无需 LLM 调用
- **`development_workflows` 和 `testing_guide`** — 硬编码模板，极少变更

### 渐进式加载（MCP 工具）

MCP Server 提供粒度化的查询工具，AI Agent 只需加载所需内容：

```
Agent 首次调用：    get_architecture_overview("vllm-ascend")        → ~1KB 概览
Agent 二次调用：    get_module_info("vllm-ascend", "attention")     → ~1KB 模块详情
Agent 三次调用：    get_interface_surface("vllm")                   → ~8KB 接口面
按需调用：          get_development_workflows("vllm-ascend")        → ~2KB 工作流
                    get_implementation_principles("vllm")           → ~5KB 实现原理
                    get_patch_catalog("platform")                   → 仅 patch 列表
```

不再需要加载完整的 90KB `architecture.json` 只为了找一个信息。

## MCP Server 工具列表

MCP Server（`src/mcp_server_app.py`）提供 **28 个工具**，分为四类：

**架构分析（13 个）：**
- `get_architecture_overview` — **[渐进式]** 返回架构概览，首次调用入口
- `get_module_info` — **[渐进式]** 返回单个模块详情，支持模糊匹配
- `get_interface_surface` — **[渐进式]** 返回接口面（可继承接口 + not_used_by_ascend 路径）
- `get_key_abstractions` — **[渐进式]** 返回关键类列表（含继承关系和方法签名）
- `get_implementation_principles` — **[渐进式]** 返回实现原理
- `get_hardware_abstraction` — **[渐进式]** 返回硬件适配层信息
- `get_development_workflows` — **[渐进式]** 返回开发工作流模板
- `get_testing_guide` — **[渐进式]** 返回测试指南
- `get_architecture_context` — **[全量]** 返回完整 architecture.json
- `get_architecture_at_commit` — 指定 commit 时的架构快照
- `get_architecture_diff` — 两 commit 间架构差异
- `get_architecture_freshness` — 检查架构数据时效性
- `get_commit_arch_delta` — 单个 commit 的架构增量影响

**适配管理（5 个）：**
- `get_adaptation_baseline` — 返回当前 vllm 基线
- `advance_baseline` — 推进基线（自动将新基线前的 commit 标记为 adapted）
- `get_pending_adaptations` — 获取待适配 commit 列表
- `get_adaptation_guide` — 获取某个 commit 的适配指南（markdown 格式）
- `get_adaptation_roadmap` — 从 SHA_from 到 SHA_to 的完整适配路线

**变更分析（6 个）：**
- `get_ascend_impact_summary` — 日期范围内影响 ascend 的 commit 摘要
- `get_commit_diff` — 获取某个 commit 的完整 diff
- `get_commit_impact_batch` — 批量查询一批 vllm commit 的 ascend 影响分析（结构化 JSON，用于 PI 决策确定性路由）
- `search_analysis` — 跨日期搜索（关键词/标签/日期范围）
- `get_daily_analysis` — 返回指定日期的分析数据
- `get_module_history` — 获取某模块的变更历史

**工程支持（2 个）：**
- `get_cross_project_mapping` — vllm ↔ vllm-ascend 跨项目映射
- `get_patch_catalog` — 返回 vllm-ascend patch 目录（可选按 platform/worker 分类筛选）

详见 [docs/mcp-usage-guide.md](docs/mcp-usage-guide.md) 的使用场景说明。

## 许可证

Apache 2.0