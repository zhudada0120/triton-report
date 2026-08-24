# vllm-report → triton-report 适配清单

> 本清单记录把 vllm-report 改造为 triton-report 时**所有需要适配的点**，用于检查移植完整性和理解两仓差异。
> 状态标记：✅ 已完成并验证 / ✅ 已完成（效果待 LLM 真跑验证）/ ⏳ 未完成
> 生成日期：2026-08-21

## 〇、适配总览

| 类别 | 数量 | 状态 |
|------|------|------|
| 一、仓库配置类（机械替换） | 9 个文件 | ✅ |
| 二、结构差异适配（实质重写） | 4 处 | ✅ |
| 三、prompt / 知识模板 | 6 处 | ✅（效果待真跑） |
| 四、前端 UI | 2 个文件 | ✅ |
| 五、CI 与脚本 | 5 个文件 | ✅ |
| 六、文档 | 6 个文件 + 3 个新增 | ✅ |
| 七、测试 | 2 个文件 | ✅ |
| 九、git 与仓库运维 | 4 项 | ✅ |
| 十、未完成/待验证项 | 6 项 | ⏳ |

---

## 一、仓库配置类（机械替换，✅）

核心模式：`vllm-project/vllm` → `triton-lang/triton`，`vllm-ascend` → `triton-ascend`，数据目录 `data/vllm*` → `data/triton*`。

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 1.1 | `src/data/_source_repo.py` | `KNOWN_REPOS` 注册表 | 两个仓库的 URL、dir_name、common_paths；新增 gitee 镜像注释（triton-ascend 国内镜像 gitee.com/ascend/triton-ascend） |
| 1.2 | `src/data/build_index.py` | `REPO_MAP`、`repos` 列表、usage 文本 | 仓库对注册 |
| 1.3 | `src/data/generate_context.py` | `REPO_SOURCE_DIRS`、`cross_repos`、docstring | source dir 两仓均改为 `.`（triton 代码分散在 python/ include/ lib/ third_party/，不能像 vllm 那样只走一个包目录） |
| 1.4 | `src/data/analyze_commits.py` | repo 引用、`is_vllm`→`is_upstream`、常量改名 `VLLM_*`→`TRITON_*`、`data_dir,"vllm"`→`"triton"` | 约 25 处机械替换 |
| 1.5 | `src/data/deep_analyze_commits.py` | prompt 中的仓库对、数据目录路径 | 含 checkout/restore 日志文本 |
| 1.6 | `src/data/fetch_commits.py` | docstring 示例 | 逻辑零改动（完全通用） |
| 1.7 | `src/data/_track_arch_delta.py` | docstring | 逻辑零改动 |
| 1.8 | `src/mcp_server_app.py` | `repo_map`、工具参数 enum、server 名、数据路径、lessons git 身份与推送 URL | 约 60 处替换 |
| 1.9 | `.github/scripts/clean_stale_data.py` | `--repo` 默认列表 | |

⚠️ **sed 陷阱**（已全部排查）：替换顺序必须先 `vllm-ascend` 再 `vllm`，否则 `vllm-project/vllm-ascend` 会错变成 `triton-project/triton-ascend`（本次移植出现过 3 处，已修复）。

---

## 二、结构差异适配（实质重写，✅）

triton-ascend 与 vllm-ascend 结构差异大，以下模块不是改名而是重设计：

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 2.1 | `src/data/_extract_patches.py` | **整体重写** | vllm-ascend 有 `vllm_ascend/patch/__init__.py` 目录清单（platform/worker patch 条目）；triton-ascend 是 `third_party/ascend/patch/*.patch` 整体式 patch 文件。改为 diffstat 确定性解析：每个 patch 的名称、大小、覆盖文件、增删行数 |
| 2.2 | `src/data/track_adaptation.py` | **整体重写** | vllm-ascend 有 baseline 文件（`.github/vllm-main-verified.commit`）划分 adapted/pending；triton-ascend **无 baseline、无 main2main**（人工 cherry-pick 回合）。改为：① git 历史扫描（上游 SHA 原样存在，cat-file --batch-check）② cherry-pick 标记扫描（`(cherry picked from commit X)`）③ 新增 `detect` 子命令（每日刷新，只提升不降级）④ 新增 `mark` 子命令（手动兜底）。移除 `--local-repo` 参数 |
| 2.3 | `src/mcp_server_app.py` 适配管理工具组 | **4 处重设计** | ① `advance_baseline`（写 baseline 文件）→ `detect_adaptation`（历史扫描检测）② `get_adaptation_baseline` 改为读 adaptation-status.json（mode/detection/stats）③ 补注册 `update_adaptation_status` 工具（原本只有实现未注册，手动 mark 刚需）④ 删除 `get_baseline_file` helper |
| 2.4 | `src/data/build_index.py` adaptation_baseline | 数据来源改变 | 原来读 ascend 仓库的 baseline 源文件；改为读 adaptation-status.json 的 baseline 对象（history-scan 模式信息） |

**调研依据**（Phase 0，真实 clone 验证）：
- 上游 SHA 2025-05~07 时期**原样存在**于 triton-ascend git 历史（早期 merge 期）✅
- 2026 年后改为人工回合，上游 SHA **不在** ascend 历史 ❌，且几乎无 `-x` cherry-pick 标记 → 手动 mark 兜底是必须的
- triton-ascend 的 `.github/` 无任何 baseline 文件

---

## 三、prompt / 知识模板（✅，效果待 LLM 真跑验证）

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 3.1 | `src/data/generate_context.py` `build_knowledge_base_template()` | **开发工作流模板全重写** | vllm 版：添加模型/配置项/平台后端 + patch/attention/envs 工作流 → triton 版：添加算子与语言特性（python/triton/language + include/ + lib/Dialect）、编译器 pass、新硬件后端（entry_points 注册）；triton-ascend 版：Ascend 后端开发（third_party/ascend/backend）、上游代码回合（cherry-pick）、算子支持、patch 维护 |
| 3.2 | 同上 `testing_guide` | 测试命令重写 | vllm 的 pytest tests/ut + ruff → triton 的 pytest/lit（`pytest python/test`）+ 注意 CANN 环境要求 |
| 3.3 | 同上 `CONTEXT_PROMPT_TEMPLATE` | "关键接口文件"列表重写 | vllm 的 platform/engine/worker/attention/scheduler → triton 的 language frontend、compiler 流水线、backends 抽象（BaseBackend/DriverBase）、runtime、include/lib IR、third_party 后端 |
| 3.4 | 同上 `TRITON_EXTRA_CONTEXT` / `ASCEND_EXTRA_CONTEXT` | 实现原理示例主题重写 | 如编译流水线 pass 顺序、后端注册机制、AscendNPU-IR lowering、上游回合方式 |
| 3.5 | 同上 `CROSS_REFERENCE_PROMPT` + `_build_cross_ref_schema` | 字段名 + 示例路径 | `vllm_to_ascend_map` → `triton_to_ascend_map`（注意：`patch_impact_map` 字段名保留——analyze_commits.py:304 消费它）；影响判断示例从 flashinfer/cuda.py 改为 third_party/nvidia、third_party/amd |
| 3.6 | `src/data/analyze_commits.py` `TRITON_ASCEND_REQUIREMENT` | 判断流程示例路径 | "纯平台特定代码（flashinfer/cuda/rocm）" → "（third_party/nvidia、third_party/amd 等）" |

**待真跑验证**：KB 模板中的 triton 测试命令是"最佳已知值"，首次 generate_context 真跑后需人工核对仓库实际测试方式；架构生成质量取决于 3.3/3.4 的 prompt 引导是否贴合 triton 结构。

---

## 四、前端 UI（✅）

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 4.1 | `site/app.js` | `REPOS` 数组、`repoDir()` 映射、`isVllm`→`isUpstream`、分类标签文本（'ascend' 等） | 数据探测策略（3 种 DATA_BASE 路径）零改动 |
| 4.2 | 同上 `loadBaseline()` | **面板重写** | 原来请求 GitHub API 读 `.github/vllm-main-verified.commit` 文件内容（triton-ascend 不存在）→ 改为只读 adaptation-status.json，显示 history-scan 模式 + pending/adapted 统计 + 检测时间 |
| 4.3 | `site/index.html` | 标题、logo、repo tab 按钮 | 纯文本 |

---

## 五、CI 与脚本（✅）

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 5.1 | `.github/workflows/daily-commit.yml` | checkout 仓库 URL（fetch-depth: 0）、opencode MCP 名、fetch/analyze 步骤 repo 参数 | Track adaptation 步骤改为：adaptation-status.json 存在 → `detect`，否则 `init`（原来是无条件 `init --force`，会丢失手动 mark） |
| 5.2 | `.github/workflows/refresh-context.yml` | 同上 | 含 cross-reference、Track adaptation 步骤 |
| 5.3 | `.github/workflows/pages.yml` | **零改动** | 无仓库引用，通用 |
| 5.4 | `daily_refresh.sh` | 仓库路径与 URL、`VLLM_REPO_PATH`→`TRITON_REPO_PATH`、`--vllm-path`→`--triton-path`、`DO_VLLM`→`DO_UPSTREAM` | ① 删除了 baseline 文件检查段（triton 无 baseline，改为打印 history-scan 说明）② Step 9 改为 detect/init 条件逻辑 |
| 5.5 | `serve.py` | 仅 description 文本 | 逻辑零改动 |

---

## 六、文档（✅）

| # | 文件 | 适配点 |
|---|------|--------|
| 6.1 | `README_CN.md` / `README.md` | 仓库链接（含 3 处错误 org `triton-project` 修复）、功能描述重写（Patch 清单提取、适配状态跟踪、适配经验库三节的机制描述）、项目结构树、快速开始命令、track_adaptation 用法（新增 detect/mark）、数据规格表（vllm 特有数字改为按项目规模）、knowledge_base 子字段表、MCP 工具列表（advance_baseline→detect_adaptation、工具数 28→29） |
| 6.2 | `docs/mcp-usage-guide.md` | 工具引用、示例命令 |
| 6.3 | `docs/sop.md` | 操作命令中的仓库名（3 处） |
| 6.4 | `docs/data-spec.md` | 文本替换（schema 字段名未动） |
| 6.5 | `docs/todo.md` | 无实质改动 |
| 6.6 | `LICENSE` | **新增**：两仓原都缺 LICENSE 文件（README 却声称 Apache 2.0）；已补官方标准全文 |
| 6.7 | `PORTING.md` | **新增**：两仓双向移植约定（差异表 + engine 文件同步清单） |
| 6.8 | `CLAUDE.md` | **新增**：项目记忆（按用户要求 gitignore，仅本地） |

---

## 七、测试（✅）

| # | 文件 | 适配点 | 说明 |
|---|------|--------|------|
| 7.1 | `tests/test_core.py` | 仓库名断言（KNOWN_REPOS、repo_dir_name） | 另外**修复了 vllm-report 也存在的坏测试**：`data.clean_stale_data` import 路径错误（模块实际在 `.github/scripts/`），建议回移 vllm-report |
| 7.2 | `tests/test_arch_delta.py` | 文本替换 | 19/19 测试通过 |

---

## 八、无需适配的通用部分（两仓保持一致，bugfix 双向同步）

以下模块与具体项目无关，移植时**零改动**，未来修 bug 需同步两仓（详见 PORTING.md）：

- `src/data/fetch_commits.py`（抓取逻辑）
- `src/data/_opencode_client.py`（opencode 封装）
- `src/data/_source_cache.py`（AST 源码上下文缓存）
- `src/data/_track_arch_delta.py`（架构增量链）
- `src/data/analyze_commits.py` / `deep_analyze_commits.py`（分析引擎逻辑本身）
- `src/mcp_server_app.py`（除适配管理工具组外的全部工具）
- `site/app.js` 渲染引擎、`serve.py`、`pages.yml`、`clean_stale_data.py` 逻辑
- **数据 schema 字段名**：`ascend_impact`、`ascend_affected`、`adaptation-status.json` 顶层结构（`{baseline, commits, stats}`）两仓一致——只有 `baseline` 内部字段不同（vllm: main_sha/release_tag；triton: mode/detection/detected_at）

---

## 九、git 与仓库运维（✅）

| # | 事项 | 说明 |
|---|------|------|
| 9.1 | 提交历史邮箱重写 | 全部提交从机器全局身份 `zhuguanda@huawei.com` 重写为 `zhuguandada@outlook.com`（filter-branch env-filter） |
| 9.2 | 仓库级 git 身份 | local config 已设 `zhudada0120 / zhuguandada@outlook.com`；**勿依赖全局配置**（本机全局身份被污染过一次） |
| 9.3 | 推送方式 | SSH remote（`git@github.com:zhudada0120/triton-report.git`）。本机是 Coder 环境：git SSH 走 `GIT_SSH_COMMAND` 包装器可用；HTTPS 直连 github.com 间歇性被墙；裸 `ssh -T` 会假性失败 |
| 9.4 | `filter-branch` 坑 | `--all` 会重写 `refs/remotes/origin/main`，导致 `--force-with-lease` 报 stale info——先 `git fetch origin` 再强推 |

---

## 十、未完成 / 待验证项（⏳）

| # | 事项 | 状态 | 说明 |
|---|------|------|------|
| 10.1 | Phase 1 / Phase 2 LLM 分析真跑 | ⏳ | 本机无 LLM_API_KEY 和 opencode CLI，未验证。首次真跑需检查：triage 路径是否合理、prompt 输出格式是否被 LLM 遵守 |
| 10.2 | 首次 `generate_context` 生成 architecture.json | ⏳ | **上线前必做**（CI 手动触发 Refresh Architecture Context 或本地跑）。验证：三、节的 prompt 引导对 triton 结构是否有效；`not_used_by_ascend` 路径清单质量（它驱动 triage 的省 token 效果） |
| 10.3 | 交叉分析（cross-reference）质量 | ⏳ | `impact_judgment_rules`（必然/可能/绝不影响路径）决定架构影响标记准确性，需人工抽查 |
| 10.4 | CI secrets 配置 | ⏳ | `DEEPSEEK_API_KEY`、`OPENCODE_AUTH_TOKEN` |
| 10.5 | GitHub Pages 启用 | ⏳ | Settings → Pages → Source 选 GitHub Actions |
| 10.6 | 浅克隆坑 | ⏳ | `daily_refresh.sh` 自动 clone 用 `--depth 1`；初始 catch-up 历史日期需完整 clone 或 `git fetch --unshallow` |
