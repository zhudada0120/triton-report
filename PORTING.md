# PORTING.md — triton-report 移植参考（源自 vllm-report）

> **2026-08-26 更新**：两仓**不再要求双向同步**。triton-report 仅以 vllm-report 为
> 移植起点，之后作为独立项目演进；本次移植期间及后续在 triton-report 上做的修改，
> 不要求回合同步到 vllm-report。本文档保留为移植历史参考（差异对照、移植方法）。

triton-report 与 vllm-report 曾是同一引擎的两个实例（上游仓 ↔ ascend 适配仓双仓监控分析）。

## 两个项目的核心差异

| 维度 | vllm-report | triton-report |
|------|-------------|---------------|
| 仓库对 | vllm-project/vllm ↔ vllm-project/vllm-ascend | triton-lang/triton ↔ triton-lang/triton-ascend |
| 数据目录 | data/vllm, data/vllm-ascend | data/triton, data/triton-ascend |
| 上游同步方式 | main2main + baseline 文件（`.github/vllm-main-verified.commit`） | 人工 cherry-pick 回合，无 baseline 文件 |
| 适配检测 | baseline SHA 之前 → adapted | git 历史扫描（SHA 原样存在 / cherry-pick 标记）+ 手动 mark |
| patch 结构 | `vllm_ascend/patch/__init__.py` 目录（平台/worker patch） | `third_party/ascend/patch/*.patch` 整体式 patch 文件 |
| 适配经验来源 | main2main E2E 修复失败 | 人工 cherry-pick 冲突修复 |
| MCP 工具 | advance_baseline（推进 baseline 文件） | detect_adaptation（历史扫描检测）+ update_adaptation_status（手动标记） |

## 移植 checklist（移植时对照用，不再要求双向同步）

Engine 级文件（两仓逻辑相同，当初移植时以此清单对照）：

1. `src/data/fetch_commits.py`
2. `src/data/analyze_commits.py`（triage、prompt 结构、LLM 调用逻辑）
3. `src/data/deep_analyze_commits.py`
4. `src/data/generate_context.py`（架构生成流程；注意 knowledge_base 模板两仓内容不同）
5. `src/data/build_index.py`
6. `src/data/_opencode_client.py`、`_source_cache.py`、`_track_arch_delta.py`
7. `src/mcp_server_app.py`（除适配管理工具组外）
8. `site/app.js` / `site/style.css`（除 REPOS 数组、baseline 面板外）
9. `serve.py`、`.github/scripts/clean_stale_data.py`

项目特有文件（**不要**互相覆盖）：

- `src/data/_extract_patches.py` — vllm 解析 patch/__init__.py；triton 解析 .patch diffstat
- `src/data/track_adaptation.py` — vllm baseline 模式；triton history-scan + cherry-pick 模式
- `generate_context.py` 中的 `build_knowledge_base_template()`、`*_EXTRA_CONTEXT`、`CROSS_REFERENCE_PROMPT`
- `daily_refresh.sh` 的 Step 9（track_adaptation 调用方式不同）
- `.github/workflows/*.yml` 的仓库 URL 与适配跟踪步骤
- README / docs 中描述适配机制的章节

## 移植方法

```bash
# 从 vllm-report 拿单个文件的最新版本
git -C ~/project/vllm-report show main:src/data/fetch_commits.py > /tmp/f.py
# 人工 diff 检查是否有项目特有内容混入，确认后再覆盖
cp /tmp/f.py src/data/fetch_commits.py
```

（反向同步自 2026-08-26 起不再要求。）

## 数据 schema 兼容性

两个项目的 data/ JSON 格式保持一致（数据字段名不变）：

- `ascend_impact.ascend_affected` 等字段名两仓相同（描述"ascend 适配"的通用概念，不是仓库名）
- `adaptation-status.json` 顶层结构相同：`{baseline, commits, stats}`；`baseline` 内部字段两仓不同
  - vllm: `{source, main_sha, release_tag, baseline_date, ...}`
  - triton: `{mode: "history-scan", detection, ascend_repo_sha, tracking_start_date, detected_at, note}`
- `commits[].status` 仅两种状态：`pending` / `adapted`（两仓一致）
- architecture.json 的 `cross_project_relationship` 中映射字段名不同：`vllm_to_ascend_map` vs `triton_to_ascend_map`
