# vllm-report 数据规范

> 定义所有数据文件的路径、格式、字段说明、兼容性保证。
> 第三方工具通过本文档了解如何读取 vllm-report 的数据。

---

## 目录结构概览

```
data/
├── README.json                      # 项目入口引导文件
├── vllm/                            # vllm 项目数据
│   ├── analysis/                    # 每日 commit 分析（按日期）
│   │   ├── 2026-07-30.json
│   │   └── ...
│   ├── commits/                     # 原始 commit 数据（按日期）
│   │   ├── 2026-07-30.json
│   │   └── ...
│   ├── _deep_analysis_cache/        # Phase 2 深分析的源码上下文缓存（AST 提取，按文件）
│   │   └── source_context/*.json
│   ├── context/
│   │   ├── architecture.json        # 项目架构知识摘要（用户主动刷新，锚定一个 commit）
│   │   └── arch_deltas.json         # 架构增量变化链（自基线以来的每个 commit 的架构影响）
│   ├── index.json                   # 检索索引（标签/模块/关键词 → SHA 列表）
│   ├── commits-index.json           # SHA → {date, message} 查找表（配合 index.json 使用）
│   └── meta.json                    # 仓库元数据
├── vllm-ascend/                     # vllm-ascend 项目数据
│   ├── analysis/
│   ├── commits/
│   ├── lessons/                     # 适配经验（main2main 实战沉淀，按日期）
│   ├── context/
│   │   ├── architecture.json
│   │   └── arch_deltas.json
│   ├── index.json
│   ├── commits-index.json
│   ├── adaptation-status.json       # 适配状态跟踪（仅 vllm-ascend）
│   └── meta.json
└── ... (更多仓库)
```

---

## 文件格式说明

### 1. `data/README.json`

**作用：** 第三方工具首次接触项目时的入口引导文件。

| 字段 | 类型 | 说明 |
|------|------|------|
| `project` | string | 项目名称 |
| `version` | integer | 文件格式版本号 |
| `description` | string | 项目描述 |
| `available_repos` | string[] | 可用仓库列表（owner/repo 格式） |
| `entry_points` | object | 入口路径映射，`{repo}` 替换为目录名 |
| `docs` | string[] | 文档路径列表 |

**兼容性：** 稳定。version 递增表示不兼容变更。

---

### 2. `data/{repo}/context/architecture.json`

**作用：** 项目的架构知识摘要，由 opencode Agent 按需生成（`refresh-context.yml` 工作流或本地 `generate_context.py`）。包含核心模块、关键抽象、接口面、跨项目关系。

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `repo` | string | 仓库名称 |
| `generated_at` | string (ISO 8601) | 生成时间（UTC+8） |
| `commit_sha` | string | 生成时基于的 commit SHA |
| `overview` | string | 项目概述 |
| `modules` | object[] | 核心模块列表 |
| `key_abstractions` | object[] | 关键抽象/类定义 |
| `implementation_principles` | object[] | 实现原理（含 workflow 描述） |
| `module_dependencies` | string | 模块间依赖关系 |
| `hardware_abstraction` | object | 硬件适配层描述 |
| `interface_surface` | object | 接口面（被外部平台继承的接口） |
| `test_structure` | object | 测试结构 |
| `cross_project_relationship` | object | 跨项目关系（vllm ↔ vllm-ascend） |
| `knowledge_base` | object (可选) | 知识库（patch_catalog、development_workflows、testing_guide） |
| `architecture_history` | object[] (可选) | 历史架构快照列表（`commit_sha` + `generated_at`），累积用于计算增量 |

**兼容性：**
- `modules`、`key_abstractions`、`interface_surface`：稳定
  - `interface_surface.not_used_by_ascend` 由 opencode 在每次架构刷新时自动更新，反映当前代码库状态
- `cross_project_relationship`：稳定
- `knowledge_base`：稳定（新增字段，向后兼容）
- `architecture_history`：实验性（聚合历史快照）

---

### 3. `data/{repo}/analysis/{date}.json`

**作用：** 每日 commit 分析结果，由 LLM 生成。

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | string (YYYY-MM-DD) | 分析日期 |
| `repo` | string | 仓库名称 |
| `generated_at` | string (ISO 8601) | 生成时间 |
| `daily_summary` | string | 当日变更总结 |
| `ascend_impact_summary` | string (可选) | 对 vllm-ascend 的影响总结（仅 vllm 仓库） |
| `test_impact_summary` | string (可选) | 测试看护影响总结（仅 vllm-ascend 仓库） |
| `architecture_based_on_sha` | string (可选) | 分析时使用的架构基于的 commit SHA |
| `architecture_generated_at` | string (可选) | 分析时使用的架构生成时间 |
| `commits` | object[] | commit 分析列表 |

**每个 commit 的字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `sha` | string | commit SHA |
| `comment` | string | 分析评论 |
| `tags` | string[] | 分类标签（类型/风险/模块） |
| `ascend_impact` | object (可选) | 对 ascend 的影响评估（仅 vllm） |
| `test_impact` | object (可选) | 测试影响评估（仅 vllm-ascend） |
| `architecture_impact` | object (可选) | 架构影响标记（修改了关键接口文件时出现） |

**ascend_impact 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `ascend_affected` | boolean | 是否影响 vllm-ascend |
| `functionality` | string | 功能影响描述 |
| `testing` | string | 测试影响描述 |
| `needs_test_update` | boolean | 是否需要更新测试 |
| `suggested_test_areas` | string[] | 建议测试变更范围 |

**architecture_impact 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `affects_architecture` | boolean | 是否影响架构 |
| `affected_interfaces` | string[] | 受影响的关键接口路径 |
| `recommend_refresh` | boolean | 是否建议刷新架构知识 |

**deep_analysis 字段（Phase 2 深度分析，仅 ascend_affected commit）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `affected_interfaces` | string[] | 具体受影响的 vllm-ascend 接口/类 |
| `adaptation_effort` | string | 适配工作量评估：low / medium / high |
| `adaptation_guide` | string | 适配指南（具体需要修改哪些文件） |
| `risk` | string | 风险评估 |
| `ascend_affected_confirmed` | boolean (可选) | 深度分析是否确认了 ascend 影响 |

**兼容性：** 稳定。`architecture_based_on_sha`、`architecture_impact`、`deep_analysis` 为新增字段，不影响旧数据。

---

### 4. `data/{repo}/index.json`

**作用：** 快速检索索引，避免遍历所有 analysis 文件。采用两层索引结构——`index.json` 存轻量 SHA 列表映射，`commits-index.json` 存 SHA→基本信息。

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `repo` | string | 仓库目录名 |
| `source_repo` | string | 源仓库名称（owner/repo） |
| `built_at` | string (ISO 8601) | 索引生成时间 |
| `total_dates` | integer | 有分析数据的天数 |
| `date_range` | string[] | 日期范围 [最早, 最晚] |
| `analysis_dates` | string[] | 所有有分析数据的日期列表（替代已废弃的 `dates.json` 和 `analysis-dates.json`） |
| `available_data` | object | 该 repo 有哪些数据可用 |
| `architecture_version` | object | 架构版本引用（`commit_sha` + `generated_at`，指向架构缓存的版本） |
| `architecture_deltas` | object | 架构增量链引用（`baseline_sha`、`delta_count`、`affected_commits`） |
| `tags_index` | object | 按 tag 索引（tag → SHA 列表），每个 commit 只存 SHA 不存完整对象 |
| `modules_index` | object | 按模块 tag 索引（模块名 → SHA 列表） |
| `architecture_impact_index` | object | 架构影响索引 |
| `keyword_index` | object | 关键词索引（commit message 分词 → SHA 列表） |
| `adaptation_baseline` | object | 基线文件引用（仅记录文件路径，不缓存 SHA） |

**tags_index / modules_index 格式：**
```json
{
  "high-risk": ["sha1", "sha2", "sha3"],
  "attention": ["sha1", "sha4"]
}
```
每个 tag 只存 SHA 字符串列表，不冗余存储 `{sha, date, message}` 对象。commit 的 `date` 和 `message` 通过 `commits-index.json` 查询。

**keyword_index 说明：** 对 commit message 做逐词拆分（空格/标点分割，统一小写），不做 NLP 分词。对英文关键词（attention、ascend、scheduler）足够用。

**adaptation_baseline 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | vllm-main-verified.commit 文件路径（相对 vllm-ascend 项目根） |
| `release_tag_source` | string | vllm-release-tag.commit 文件路径 |
| `current_sha` | string (可选) | 当前基线 SHA（运行时从源文件读取） |
| `current_release_tag` | string (可选) | 当前 release tag（运行时从源文件读取） |

**兼容性：** 稳定。`tags_index` / `modules_index` / `keyword_index` 在 2026-07 重构后改为只存 SHA 列表而非完整对象，`commits-index.json` 作为补充查询表。

---

### 5. `data/{repo}/commits-index.json`

**作用：** SHA → {date, message} 快速查找表，配合 `index.json` 的 SHA 列表使用。由 `build_index.py` 在构建索引时从 commits 目录提取生成。

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `{sha}` | object | 以 SHA 为 key 的映射 |
| `{sha}.date` | string (YYYY-MM-DD) | commit 日期 |
| `{sha}.msg` | string | commit message 第一行（截断到 120 字符） |

**格式示例：**
```json
{
  "abc123def456": {"date": "2026-07-30", "msg": "refactor attention backend interface"},
  "def789abc012": {"date": "2026-07-30", "msg": "fix scheduler memory leak"}
}
```

**兼容性：** 稳定。2026-07 新增，配合 index.json 重构使用。

---

### 6. `data/vllm-ascend/adaptation-status.json`

**作用：** 记录 vllm-ascend 的 main2main 适配进度。由 `track_adaptation.py init` 自动生成，仅维护两种状态。

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `baseline` | object | 基线信息（含 SHA 和日期） |
| `commits` | object[] | commit 适配状态列表 |
| `stats` | object | 统计汇总 |

**baseline 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | vllm-main-verified.commit 文件路径 |
| `release_tag_source` | string | vllm-release-tag.commit 文件路径 |
| `main_sha` | string | 当前基线 SHA |
| `release_tag` | string | 当前 release tag |
| `tracking_start_date` | string | 开始跟踪的日期 |
| `baseline_date` | string | 基线 commit 对应的分析日期 |

**commits 中的每个条目：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `sha` | string | vllm commit SHA |
| `upstream_date` | string | vllm 上游 commit 日期 |
| `upstream_sha` | string | vllm 上游 commit SHA |
| `message` | string | commit 消息（截断到 120 字符） |
| `status` | string | 状态：`pending`（待适配）或 `adapted`（已适配） |
| `tags` | string[] | 标签 |
| `ascend_impact_summary` | string | 影响概述 |
| `adaptation_notes` | string | 适配备注 |
| `adapted_at` | string (nullable) | 适配完成时间 |
| `adapted_by` | string (nullable) | 适配执行者（机器人/人） |

**stats 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 总计 |
| `pending` | integer | 待适配 |
| `adapted` | integer | 已适配 |

**兼容性：** 2026-08 重构，状态从 5 种精简为 2 种（pending / adapted）。旧格式的 `unknown` / `in_progress` / `skipped` 不再使用。

---

### 7. `data/{repo}/arch_deltas.json`

**作用：** 记录自架构基线以来每个 commit 对架构的增量影响。用于实现"架构知识时间旅行"——分析历史 commit 时，可以知道该 commit 发生时架构知识的样子。

**架构知识版本化模型：**
- `architecture.json` 是一个**基线快照**（用户主动刷新）
- `arch_deltas.json` 记录基线之后的每个 commit 对架构的增量变化
- 分析 prompt 中使用"基线 + 到该 commit 的增量"来还原该 commit 发生时的架构知识
- 用户刷新 `architecture.json` 时，`arch_deltas.json` 会被清空

**关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `baseline_sha` | string | 当前架构知识基线的 commit SHA |
| `baseline_generated_at` | string (ISO 8601) | 基线生成时间 |
| `deltas` | object | commit SHA → delta 信息的映射 |

**每个 delta 的字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `affected_modules` | string[] | 受影响的模块路径（如 attention/backends） |
| `affected_interfaces` | string[] | 受影响的接口名 |
| `interface_changes` | string | 接口变更描述 |
| `change_summary` | string | commit 标题（第一行） |
| `ascend_impact` | boolean | 是否影响 vllm-ascend |

**兼容性：** 实验性。2026-08 新增，用于支持架构知识时间旅行。

---

## 日期格式

- 所有日期使用 `YYYY-MM-DD` 格式
- 时区为 Asia/Shanghai (UTC+8)
- analysis 文件中的 `generated_at` 使用 ISO 8601 格式（含时区偏移）

---

## 兼容性保证

| 保证级别 | 说明 |
|---------|------|
| **稳定** | 字段名称和含义不会改变，最多增加新字段 |
| **实验性** | 字段可能在未来版本中修改或移除，会提前在文档中标注 |
| **不保证** | 临时数据，不对外提供兼容性 |

所有当前文档中列出的字段均为 **稳定** 级别，除已单独标注为 **实验性** 的字段（如 `architecture_history`）外。