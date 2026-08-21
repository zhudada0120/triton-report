# vllm-report 使用指南 — AI Agent 驱动的 main2main 适配

> 第三方 main2main 工具（OpenCode、Codex CLI 等）
> 如何利用 vllm-report 知识库进行 vllm-ascend 代码升级和适配

---

## 前置条件

1. vllm-report 项目数据已正常生成（index.json、arch.json、analysis JSON 等）
2. MCP Server 已配置（OpenCode 场景）或可直接读取 JSON 文件（其他工具场景）

---

## 第三方工具对接方式

### 方式一：OpenCode（原生 MCP）

OpenCode 完全支持 MCP 协议，在项目 `opencode.json` 或 `opencode.jsonc` 中配置：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "vllm-report": {
      "type": "local",
      "command": ["python", "-m", "src.mcp_server_app", "--data-dir", "/path/to/vllm-report/data", "--ascend-repo-path", "/path/to/vllm-ascend"],
      "enabled": true
    }
  }
}
```

或在 `~/.config/opencode/opencode.json` 中全局配置。配置后重启 opencode 生效。

也可用命令添加：

```bash
opencode mcp add vllm-report -- python -m src.mcp_server_app \
    --data-dir /path/to/vllm-report/data \
    --ascend-repo-path /path/to/vllm-ascend
```

`--ascend-repo-path` 用于读取 vllm-ascend 项目中的基线文件（`.github/vllm-main-verified.commit` 和 `.github/vllm-release-tag.commit`），使 MCP Server 能回答"当前已验证到哪个 commit"的问题。

配置后，在 vllm-ascend 项目目录下打开 opencode，自动拥有所有知识库能力。可用 `opencode mcp list` 验证连接状态。

### 方式二：HTTP 包装（任意工具）

用 socat 将 MCP Server 包装为 TCP 服务：

```bash
socat TCP-LISTEN:9999,reuseaddr,fork \
  EXEC:"python -m src.mcp_server_app --data-dir /path/to/vllm-report/data --ascend-repo-path /path/to/vllm-ascend"
```

---

## 场景一：日常巡检

> 每天查看 vllm 上游有哪些 commit 会影响 vllm-ascend

```
今天 vllm 有哪些影响 ascend 的 commit？
```

Agent 内部：
1. 调用 `get_daily_analysis("vllm", "2026-07-31")`
2. 筛选 `ascend_impact.ascend_affected === true` 的 commit
3. 按风险排序展示

**输出示例：**

```
今日（2026-07-31）vllm 有 34 个 commit，其中 3 个影响 ascend：

1. [high-risk] refactor AttentionBackend.forward() 签名
   → 影响：AscendAttentionBackend 需要适配新的 forward 参数
   → 对应 patch：attention_v1.py

2. [medium-risk] Scheduler 新增 max_num_seqs 校验
   → 影响：patch_balance_schedule.py 可能需要更新

3. [low-risk] 更新 CI 配置
   → 不影响 ascend，跳过
```

---

## 场景二：main2main 版本同步

> 准备将 vllm main 的最新代码同步到 vllm-ascend，查看还有哪些没适配

```
上次同步是 7月15日，我要做一次 main2main 同步，帮我看看还有哪些没适配的？
```

Agent 内部：
1. 调用 `get_adaptation_baseline()` 获取当前已验证的基线
2. 调用 `get_ascend_impact_summary("vllm", baseline_date, today)` 获取基线之后影响 ascend 的 commit
3. 调用 `get_pending_adaptations()` 查看当前适配进度
4. 对比得出"需要适配但还没做"的列表

**输出示例：**

```
当前基线信息：
  main 已验证 SHA: d02df748b
  基线之后: 127 个 commit，其中 12 个影响 ascend

最近同步时间：2026-07-15
当前最新分析：2026-07-31
间隔：16 天

这段时间有 12 个 commit 影响 ascend：
  - 5 个已适配
  - 1 个跳过（仅文档变更）
  - 6 个待适配

待适配列表（按风险排序）：
1. [high-risk] refactor AttentionBackend.forward() - 7月30日
   架构影响：AttentionBackend 接口变更
   建议适配步骤：
     a. 查看 commit diff
     b. 修改 vllm_ascend/attention/attention_v1.py
     c. 运行相关测试

2. [medium-risk] Scheduler max_num_seqs 校验 - 7月28日
   ...

是否开始适配第 1 个？
```

---

## 场景三：适配单个 commit

> 针对某个具体的 commit，了解改动详情并进行适配

```
帮我适配这个 commit：abc123，它改了 AttentionBackend 的 forward 方法
```

Agent 内部：
1. 调用 `get_daily_analysis("vllm", "2026-07-30")` 获取该 commit 的分析
2. 从 arch.json 的 `knowledge_base.patch_catalog` 中找到对应的 patch
3. 从 `knowledge_base.development_workflows` 中找到适配步骤
4. 从 `cross_project_relationship.patch_impact_map` 确认影响范围
5. 调用 `get_adaptation_lessons(keywords=[改动主题 / 相关错误])` 查询是否已有类似经验 —— 命中则直接参考 fix_guidance，避免踩已踩过的坑

**输出示例：**

```
commit abc123 - refactor AttentionBackend.forward()

变更内容：
- 修改了 vllm/v1/attention/backend.py
- forward() 方法新增参数 `cache_config`
- 返回类型从 Tensor 改为 Tuple[Tensor, dict]

影响分析：
- 必然影响 ascend：AscendAttentionBackend 继承 AttentionBackend
- 对应 patch：vllm_ascend/attention/attention_v1.py
- 需要修改：forward 方法签名 + 返回值处理

建议适配步骤：
1. 打开 vllm_ascend/attention/attention_v1.py
2. 将 forward 方法添加 `cache_config` 参数
3. 调整返回值处理逻辑
4. 运行测试：pytest -sv tests/ut/attention/
5. 适配状态由下一次 `track_adaptation.py init` 自动更新
```


---

## 场景三.5：E2E 修复失败（适配后测试不过）

> 适配后 E2E 测试失败，**重新诊断前必须先查已沉淀的经验**

```
E2E 报错：AssertionError: Failed to apply prompt replacement for mm_items['image'][0]
```

Agent 内部（**顺序强制**）：
1. **先**调用 `get_adaptation_lessons(keywords=["Failed to apply prompt replacement"])`
   —— 命中则直接按返回的 `fix_guidance` 执行，不再从头诊断
2. 未命中时再读 traceback 分析（注意 multi-path：cache / no-data 路径的修复与正常路径不同）
3. 修好后若发现是"第一次修不对"的新陷阱，调用 `submit_lesson` 沉淀
   （main2main_flow 也会在 step 需要 E2E 修复轮次时自动沉淀）


---

## 场景四：架构变更评估

> 评估某个核心接口变更的影响范围

```
这个 commit 改了 vllm/platforms/interface.py，影响有多大？
```

Agent 内部：
1. 分析 commit diff，检测到修改了 `definitely_affected_paths` 中的文件
2. 调用 `get_architecture_freshness()` 查看 arch.json 是否过时
3. 调用 `get_cross_project_mapping()` 获取跨项目映射
4. 从 `knowledge_base.patch_catalog` 中列出所有关联 patch

**输出示例：**

```
这个 commit 修改了 Platform 基类的 get_device_capability() 返回值类型

影响范围（必然影响）：
→ NPUPlatform（vllm_ascend/platform.py）需要同步修改

涉及的 patch：
- 无直接 patch，但 NPUPlatform 继承 Platform，必须适配
- 影响所有平台相关的 worker patch（24 个 platform patch）

建议：
1. 先刷新架构知识（当前 arch.json 基于 3 天前的代码，可能已过时）
2. 然后分析具体影响范围
3. 最后逐个适配
```

---

## 场景五：按模块查询变更历史

> 开发中需要了解某个模块近期的变更趋势

```
最近 attention 模块有什么变更？
```

Agent 内部：
1. 调用 `get_module_history("vllm", "attention", 14)`
2. 通过 index.json 的 `modules_index` 快速定位
3. 读取对应 analysis 文件获取详情

**输出示例：**

```
过去 14 天 attention 模块有 5 个变更：

1. 7月30日 - refactor AttentionBackend.forward() [high-risk]
   ⚡ 影响 ascend：是 → patch：attention_v1.py 需要适配

2. 7月28日 - fix MLA attention bias [bugfix]
   ⚡ 影响 ascend：是 → patch：mla_v1.py 需要同步修复

3. 7月25日 - add FlashInfer fallback [feature]
   ⚡ 影响 ascend：否（FlashInfer 是 CUDA 特有）

4. 7月22日 - refactor attention metadata builder [refactor]
   ⚡ 影响 ascend：否（接口未变，仅内部重构）

5. 7月20日 - update attention selector [refactor]
   ⚡ 影响 ascend：需要关注（selector 逻辑影响后端选择）
```

---

## 场景六：查询架构知识

> 快速了解 ascend 某个模块的架构设计

```
ascend 的 attention 后端有哪些？和 vllm 的 AttentionBackend 是什么关系？
```

Agent 内部：
1. 调用 `get_architecture_context("vllm-ascend")`
2. 从 `modules` 中提取 Attention 模块
3. 从 `key_abstractions` 中提取继承关系
4. 从 `knowledge_base.patch_catalog` 中提取 attention 相关 patch

**输出示例：**

```
Ascend Attention 后端：

实现文件：
- vllm_ascend/attention/attention_v1.py — V1 主逻辑
- vllm_ascend/attention/mla_v1.py — MLA 实现
- vllm_ascend/attention/fa3_v1.py — Flash Attention 3
- vllm_ascend/attention/dsa_v1.py — DSA

继承关系：
- AscendAttentionBackend → vllm AttentionBackend（vllm/v1/attention/backend.py）

关键差异：
- 使用 NZ 格式的 KV cache 布局（与 CUDA 不同）
- 通过 ACL Graph 而非 CUDA Graph 捕获计算图
- 有自己的 attention 元数据构建器

相关 patch（4 个）：
- patch_deepseek_mtp.py — DeepSeek MTP attention
- patch_minimax_m2_linear_attn.py — MiniMax-M2 linear attention
- patch_qwen3_dflash.py — Qwen3 DFlash attention
- patch_gqa_c8.py — GQA C8 优化
```

---

## 场景七：查询测试指南

> 适配完成后，需要运行测试验证

```
怎么运行 ascend 的 attention 相关测试？
```

Agent 内部：
1. 调用 `get_architecture_context("vllm-ascend")`
2. 从 `knowledge_base.testing_guide` 中提取测试命令

**输出示例：**

```
Ascend attention 测试：

单元测试：
  pytest -sv tests/ut/attention/

端到端测试（需要 NPU 硬件）：
  pytest -sv tests/e2e/pull_request/one_card/

没有 NPU 硬件时，部分 attention 单测可在 CPU 上运行：
  VLLM_ASCEND_USE_CPU_ATTENTION=1 pytest -sv tests/ut/attention/
```

---

## 工作流总结

### 每日流程

```
1. 巡检 → "今天有哪些影响 ascend 的 commit？"
2. 评估 → 逐个查看影响范围
3. 适配 → 对需要适配的 commit 执行适配
4. 标记 → 无需操作，下一次 `track_adaptation.py init` 会自动更新状态
```

### 版本同步流程

```
1. 基线 → "当前基线是哪个 commit？"（get_adaptation_baseline）
2. 汇总 → "基线之后还有哪些没适配？"
3. 分类 → 按风险排序，优先处理 high-risk
4. 适配 → 逐个 commit 适配
5. 验证 → 运行测试
6. 标记 → 全部标记完成
7. 更新基线 → main2main CI 完成后自动更新 vllm-main-verified.commit
```

### 架构变更流程

```
1. 检测 → commit 修改了关键接口文件
2. 评估 → get_architecture_freshness() + get_cross_project_mapping()
3. 刷新 → 必要时触发架构知识刷新
4. 适配 → 逐个受影响组件适配
5. 验证 → 运行完整测试套件
```

## MCP 工具速查表

> 以下为 vllm-report MCP Server 提供的全部工具，按类别组织。

### 架构分析（13 个）

| 工具 | 渐进式 | 说明 |
|------|--------|------|
| `get_architecture_overview` | ✅ | 概览 + 模块列表（轻量级，首选） |
| `get_module_info` | ✅ | 单个模块详情（支持模糊匹配） |
| `get_interface_surface` | ✅ | 可继承接口 + not_used_by_ascend 路径 |
| `get_key_abstractions` | ✅ | 关键抽象类 + 继承关系 + ascend 实现 |
| `get_implementation_principles` | ✅ | 核心工作流详解 |
| `get_hardware_abstraction` | ✅ | 平台无关 vs 平台特定代码划分 |
| `get_development_workflows` | ✅ | 开发工作流模板 |
| `get_testing_guide` | ✅ | 测试命令和环境配置 |
| `get_architecture_context` | ❌ | 完整架构数据（量大，可能截断，按需使用） |
| `get_architecture_at_commit` | ❌ | 指定 commit 时的架构快照 |
| `get_architecture_diff` | ❌ | 两 commit 间架构差异 |
| `get_architecture_freshness` | ❌ | 架构数据是否过时 |
| `get_commit_arch_delta` | ❌ | 单个 commit 对架构的增量影响 |

### 适配管理（5 个）

| 工具 | 说明 |
|------|------|
| `get_adaptation_baseline` | 获取当前已验证的 vllm 基线 commit |
| `advance_baseline` | 推进基线（自动将新基线前的 pending commit 标记为 adapted） |
| `get_pending_adaptations` | 获取待适配（pending）的 commit 列表 |
| `get_adaptation_guide` | 单个 commit 的详细适配指南（含影响分析、测试命令） |
| `get_adaptation_roadmap` | 从 SHA_from 到 SHA_to 的完整适配路线 |

### 变更分析（6 个）

| 工具 | 说明 |
|------|------|
| `get_ascend_impact_summary` | 日期范围内影响 ascend 的 commit 摘要 |
| `get_commit_diff` | 获取 commit 全量 diff（本地优先，GitHub API 回退） |
| `get_commit_impact_batch` | 批量查询一批 vllm commit 的 ascend 影响分析（结构化 JSON） |
| `search_analysis` | 跨日期关键词/标签搜索 |
| `get_daily_analysis` | 指定日期的分析数据 |
| `get_module_history` | 模块近期变更历史（可指定天数） |

### 工程支持（2 个）

| 工具 | 渐进式 | 说明 |
|------|--------|------|
| `get_cross_project_mapping` | ❌ | vllm ↔ vllm-ascend 跨项目映射 |
| `get_patch_catalog` | ❌ | vllm-ascend 所有 patch 的完整目录（可分类筛选） |

### 经验沉淀（2 个）

> 实战适配经验（E2E 反复失败后修好的陷阱、新发现的模式），由 main2main_flow 在 step 需要 E2E 修复轮次时自动沉淀，也可由 agent 主动提交。

| 工具 | 说明 |
|------|------|
| `get_adaptation_lessons` | 按 keywords/tags 检索已记录的经验（symptom / root_cause / fix_guidance）。**E2E 失败重新诊断前必须先调用**——命中则直接按经验修复，一次到位 |
| `submit_lesson` | 提交一条经验（title/root_cause/fix_guidance 必填），追加到当日 lessons/<date>.json |

### 渐进式加载模式

```
第一步：get_architecture_overview(repo)
  → 返回模块列表 + 概述，快速了解项目结构

第二步：按需深入
  ├─ get_module_info(repo, module_name)   → 单个模块详情
  ├─ get_key_abstractions(repo)           → 类级继承关系
  ├─ get_interface_surface(repo)          → 接口表面
  └─ get_implementation_principles(repo)  → 工作流细节

第三步：特定场景
  ├─ 适配 commit → get_adaptation_guide(sha)    → 适配指南
  ├─ 查 patch    → get_patch_catalog()          → patch 目录
  ├─ 跑测试      → get_testing_guide(repo)      → 测试命令
  └─ 开发新功能  → get_development_workflows(repo) → 工作流模板
```

### 数据量说明

- `get_architecture_context` 和 `get_architecture_at_commit` 返回完整架构数据，可能被截断（超过 ~25KB）
- 优先使用渐进式接口（标注 ✅）避免信息过载

### 空结果说明

以下情况返回空结果属于正常行为：
- 基线未推进时 `get_architecture_diff` 返回 0 delta
- 无分析数据时 `get_daily_analysis` 返回 error
- 无匹配时 `search_analysis` 返回 0 results
- 同一 commit 的 `get_commit_arch_delta` 可能无 delta 数据

---

## 与现有工具的关系

| 工具 | 角色 | 说明 |
|------|------|------|
| **Web Dashboard**（site/） | 人工浏览 | 不变，继续使用 |
| **MCP Server**（src/mcp_server_app.py） | Agent 查询入口 | 28 个 tools，知识库核心接口 |
| **track_adaptation.py** | 适配进度管理 | CLI 工具，init/status/list/backfill-messages |
| **build_index.py** | 检索索引 | 每日更新，加速跨日期搜索 |
| **index.json + commits-index.json** | 两层检索索引 | index.json 存 SHA 列表映射，commits-index.json 存 SHA→基本信息 |
| **architecture.json** | 架构知识库（12 维度） | 含 overview、modules、key_abstractions、implementation_principles、interface_surface、cross_project_relationship、knowledge_base、architecture_history |
| **vllm-knowledge** | 手写知识库 | **已废弃**，内容已合并到 architecture.json |