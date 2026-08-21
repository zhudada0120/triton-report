# vllm-report 项目 SOP

## 项目结构

```
data/
├── README.json                          # 项目入口引导文件
├── vllm/
│   ├── commits/{date}.json              # 原始 commit 数据（含 diff/patch）
│   ├── analysis/{date}.json             # Phase 1 DeepSeek 分析结果
│   ├── _deep_analysis_cache/            # Phase 2 深分析的源码上下文缓存（AST 提取）
│   ├── context/
│   │   ├── architecture.json            # vllm 架构基线知识库
│   │   └── arch_deltas.json             # 自基线以来的架构增量变更
│   ├── index.json                       # 检索索引
│   └── commits-index.json               # SHA → {date, message} 查找表
└── vllm-ascend/
    ├── commits/{date}.json
    ├── analysis/{date}.json
    ├── lessons/{date}.json              # 适配经验沉淀（main2main 实战）
    ├── context/
    │   ├── architecture.json            # vllm-ascend 架构知识（基于最新代码）
    │   └── arch_deltas.json
    ├── index.json
    ├── commits-index.json
    └── adaptation-status.json           # 适配状态跟踪
```

**核心概念：**
- **vllm 架构知识**采用"基线 + 增量"模型：`architecture.json` 是基线快照（锚定于一个 commit），`arch_deltas.json` 记录基线之后每个 commit 的架构增量
- **vllm-ascend 架构知识**同样采用"基线 + 增量"模型，记录 ascend 自身的代码变化
- 所有 git checkout/pull 操作都由脚本自动完成，用户只需提供本地仓库路径

---

## 1. 首次初始化（data/ 为空时）

### 1.1 确定基线 commit

选择一个 vllm 的 commit 作为架构基线的锚点。通常选择分析起始日前一天的最后一个 commit。

```bash
cd ~/code/vllm
git log --before="2026-07-27 00:00:00 +0800" --oneline --format="%H %ai %s" -1
# 输出: 7154856f3dcb ... [Bugfix] Fix handling 5D KV cache
```

### 1.2 生成 vllm 架构基线

`--checkout` 指定基线 commit，脚本会自动 pull 最新代码、临时 checkout 到该 commit、生成架构、恢复 HEAD。

```bash
cd ~/code/vllm-report
python3 src/data/generate_context.py \
  --repo vllm-project/vllm \
  --local-repo ~/code/vllm \
  --checkout <baseline_sha> \
  --force
```

**验证：**

```bash
python3 -c "
import json
with open('data/vllm/context/architecture.json') as f:
    d = json.load(f)
print(f'commit_sha: {d[\"commit_sha\"][:12]}')
print(f'modules: {len(d[\"modules\"])}')
print(f'key_abstractions: {len(d[\"key_abstractions\"])}')
print(f'interface_surface: {bool(d.get(\"interface_surface\"))}')
"
python3 -c "
import json
with open('data/vllm/context/arch_deltas.json') as f:
    d = json.load(f)
print(f'baseline: {d[\"baseline_sha\"][:12]} deltas: {len(d[\"deltas\"])}')  # 应为 0
"
```

### 1.3 生成 vllm-ascend 架构知识

vllm-ascend 不需要历史代码，直接用最新 main 分支生成。

```bash
cd ~/code/vllm-report
python3 src/data/generate_context.py \
  --repo vllm-project/vllm-ascend \
  --local-repo ~/code/vllm-ascend \
  --force
```

**验证：**

```bash
python3 -c "
import json
with open('data/vllm-ascend/context/architecture.json') as f:
    d = json.load(f)
print(f'commit_sha: {d.get(\"commit_sha\",\"N/A\")[:12]}')
print(f'modules: {len(d.get(\"modules\",[]))}')
print(f'interface_surface inheritable_interfaces: {len(d.get(\"interface_surface\",{}).get(\"inheritable_interfaces\",[]))}')
"
```

### 1.4 生成交叉引用

交叉引用会读取两个架构文件，让 opencode 分析跨项目关系。用 `--checkout` 确保 vllm 源码在基线 commit 状态，opencode 可以探索源码来生成更准确的 `definitely_affected_paths` 等规则。

```bash
cd ~/code/vllm-report
python3 src/data/generate_context.py \
  --cross-reference \
  --checkout <baseline_sha> \
  --force
```

**验证：**

```bash
python3 -c "
import json
with open('data/vllm/context/architecture.json') as f:
    d = json.load(f)
cpr = d.get('cross_project_relationship', {})
print(f'vllm_to_ascend_map: {len(cpr.get(\"vllm_to_ascend_map\",{}))} entries')
print(f'definitely_affected_paths: {len(cpr.get(\"impact_judgment_rules\",{}).get(\"definitely_affected_paths\",[]))} entries')
print(f'never_affected_paths: {len(cpr.get(\"impact_judgment_rules\",{}).get(\"never_affected_paths\",[]))} entries')
print(f'patch_impact_map: {len(cpr.get(\"patch_impact_map\",{}))} entries')
"
```

### 1.5 首次获取 commit 数据并分析

```bash
# 获取某天的 commit 数据
cd ~/code/vllm-report
python3 src/data/fetch_commits.py \
  --repo vllm-project/vllm \
  --local-repo ~/code/vllm \
  --date 2026-07-27

# Phase 1: DeepSeek 分析
python3 src/data/analyze_commits.py \
  --repo vllm-project/vllm \
  --date 2026-07-27 \
  --local-repo ~/code/vllm \
  --force

# Phase 2: opencode 深度分析（针对 ascend_affected 的 commit）
python3 src/data/deep_analyze_commits.py \
  --repo vllm-project/vllm \
  --date 2026-07-27 \
  --local-repo ~/code/vllm \
  --data-dir data
```

**验证 Phase 1 结果：**

```bash
python3 -c "
import json
with open('data/vllm/analysis/2026-07-27.json') as f:
    d = json.load(f)
print(f'commits: {len(d[\"commits\"])}')
ascend = [c for c in d['commits'] if c.get('ascend_impact',{}).get('ascend_affected')]
print(f'ascend_affected: {len(ascend)}')
missing = [c for c in d['commits'] if c.get('comment') == '（分析缺失）']
print(f'分析缺失: {len(missing)}')  # 应该为 0
for c in ascend:
    print(f'  {c[\"sha\"][:12]} {c.get(\"message\",\"\")[:60]}')
"
```

**验证 Phase 2 结果：**

```bash
python3 -c "
import json
with open('data/vllm/analysis/2026-07-27.json') as f:
    d = json.load(f)
deep = [(c['sha'][:12], c.get('deep_analysis',{}).get('adaptation_effort','?')) for c in d['commits'] if c.get('deep_analysis')]
print(f'deep_analysis: {len(deep)}')
for sha, effort in deep:
    print(f'  {sha} effort={effort}')
"
```

### 1.6 构建索引

```bash
python3 src/data/build_index.py --data-dir data
```

### 1.7 初始化适配跟踪

扫描所有 `ascend_affected=true` 的 commit，根据基线自动划分状态：
- 基线之前的 commit → **adapted**（已被基线覆盖验证）
- 基线之后的 commit → **pending**（需要适配）

```bash
python3 src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend \
  --data-dir data \
  --since 2026-07-27
```

验证：
```bash
python3 src/data/track_adaptation.py status
# 输出示例：
#   总计:    47
#   ✅ 已适配: 42  （基线之前的，自动标记）
#   ⏳ 待适配: 5   （基线之后的，需要处理）

python3 src/data/track_adaptation.py list --status pending
# 列出所有待适配的 commit
```

---

## 2. 每日例行更新

### 2.1 vllm

```bash
# 获取前一天 commit 数据
cd ~/code/vllm-report
DATE=$(TZ=Asia/Shanghai date -d yesterday +%Y-%m-%d)

python3 src/data/fetch_commits.py \
  --repo vllm-project/vllm \
  --local-repo ~/code/vllm \
  --date $DATE

# Phase 1: DeepSeek 分析
python3 src/data/analyze_commits.py \
  --repo vllm-project/vllm \
  --date $DATE \
  --local-repo ~/code/vllm \
  --force

# Phase 2: opencode 深度分析
python3 src/data/deep_analyze_commits.py \
  --repo vllm-project/vllm \
  --date $DATE \
  --local-repo ~/code/vllm \
  --data-dir data
```

### 2.2 vllm-ascend

```bash
python3 src/data/fetch_commits.py \
  --repo vllm-project/vllm-ascend \
  --local-repo ~/code/vllm-ascend \
  --date $DATE

python3 src/data/analyze_commits.py \
  --repo vllm-project/vllm-ascend \
  --date $DATE \
  --force
```

### 2.3 构建索引

```bash
python3 src/data/build_index.py --data-dir data
```

### 2.4 更新适配状态

根据最新的 vllm-ascend 基线重新划分适配状态：
- 基线之前的 commit → **adapted**（已被基线覆盖验证）
- 基线之后的 commit → **pending**（需要适配）

```bash
python3 src/data/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend \
  --data-dir data \
  --force
```

---

## 3. 刷新架构基线

当 `arch_deltas.json` 累积了较多增量（建议 30-50 个 commit 后），或者 vllm 发生了重大架构变更时，应该刷新基线。

刷新基线的本质：用最新代码重新生成 `architecture.json`，清空 `arch_deltas.json`。

### 3.1 重新生成 vllm 架构

```bash
cd ~/code/vllm-report
python3 src/data/generate_context.py \
  --repo vllm-project/vllm \
  --local-repo ~/code/vllm \
  --force
```

不传 `--checkout` 时，脚本基于当前 HEAD 生成架构（即最新 main）。自动重置 `arch_deltas.json` 为新的基线。

### 3.2 重新生成交叉引用

```bash
python3 src/data/generate_context.py \
  --cross-reference \
  --force
```

### 3.3 验证

```bash
python3 -c "
import json
with open('data/vllm/context/architecture.json') as f:
    d = json.load(f)
print(f'新基线: {d[\"commit_sha\"][:12]}')
print(f'版本历史: {len(d.get(\"architecture_history\",[]))} 次')
with open('data/vllm/context/arch_deltas.json') as f:
    d = json.load(f)
print(f'deltas 已清空: {len(d[\"deltas\"])}')  # 应该为 0
"
```

### 3.4 注意事项

- 刷新基线**不会影响**已有的分析结果（`analysis/{date}.json`）。这些分析仍然有效，只是它们引用的 `architecture_based_on_sha` 是旧基线。
- 刷新基线后 `arch_deltas.json` 被清空，之前累积的增量丢失。这是预期的——新基线已经包含了这些变更。
- 刷新基线后**建议重新构建索引**：`python3 src/data/build_index.py --data-dir data`
- 刷新基线后**建议重新初始化适配跟踪**：`python3 src/data/track_adaptation.py init --ascend-repo-path ~/code/vllm-ascend --data-dir data --since <新基线日期>`。因为 `ascend_affected` 的判断可能因架构知识变化而不同，新基线可能标记出不同的 commit。
- vllm-ascend 的架构知识**不需要**刷新，它始终基于最新代码。

## 4. 注意事项

### 4.1 索引构建

每天更新数据后都需要构建索引（`build_index.py`）。它读取所有 `analysis/{date}.json` 文件重建索引，写入 `index.json` 和 `commits-index.json`。这个过程是幂等的——多次运行结果相同。

### 4.2 适配跟踪

`track_adaptation.py init` 扫描 `analysis/` 中所有 `ascend_affected=true` 的 commit，根据 vllm-ascend 的 `vllm-main-verified.commit` 自动划分状态：
- 基线之前的 commit → `adapted`（已被基线覆盖验证）
- 基线之后的 commit → `pending`（需要适配）

**每次 fetch + analyze 后都必须重新 `init`**，因为：
1. vllm-ascend 的基线可能已推进（main2main CI 更新了 `vllm-main-verified.commit`）
2. 新 commit 需要加入跟踪

**适配状态仅维护两种：`pending` / `adapted`，无需人工干预。**

### 4.3 重建基线的连锁影响

| 重建 base 后 | 需要做什么 |
|-------------|-----------|
| `architecture.json` | 自动更新（`--force` 时） |
| `arch_deltas.json` | 自动清空 |
| `analysis/{date}.json` | 不需要动（`architecture_based_on_sha` 记录版本） |
| `index.json` / `commits-index.json` | 需要重新构建 |
| `adaptation-status.json` | 建议重新 `init` |
| `vllm-ascend/architecture.json` | 不需要动 |

---

## 脚本说明

| 脚本 | 作用 | 调用频率 | 自动 git 操作 |
|------|------|----------|--------------|
| `fetch_commits.py` | 从本地 git 获取某天的 commit 数据（含 diff/patch） | 每日 | pull |
| `analyze_commits.py` | Phase 1: DeepSeek 批量分析 commit | 每日 | 无 |
| `deep_analyze_commits.py` | Phase 2: opencode 深度分析 ascend_affected commit | 每日（仅当有 ascend_affected 时） | checkout 到父 commit 再恢复 |
| `generate_context.py` | 生成/刷新架构知识库 + 交叉引用 | 初始化时 / 用户主动刷新 | pull（--checkout 时）+ checkout 再恢复 |
| `build_index.py` | 构建检索索引 | 每次数据更新后 | 无 |
| `track_adaptation.py` | 管理适配状态跟踪（init/status/list/backfill-messages） | 每次数据更新后 | 无 |
| `mcp_server_app.py` | MCP 服务（供 agent 查询知识库，运行 `python -m src.mcp_server_app`） | 常驻 | 无 |
| `clean_stale_data.py` | 清理过期数据（CI 专用） | 每日（CI 中） | 无 |

> `track_adaptation.py` 另有一个 `backfill-messages` 子命令，用于从 `commits-index.json` 回填适配跟踪中缺失的 commit message。
>
> 也可用根目录 `daily_refresh.sh` 一次跑完整个每日流水线（对应 `daily-commit.yml`），可用 `--repo/--date/--skip-*` 等参数控制步骤。