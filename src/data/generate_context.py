#!/usr/bin/env python3
"""
生成 vllm / vllm-ascend 项目的架构知识库（architecture.json）。

用法：
  # 生成 vllm 架构基线（锚定指定 commit）
  python src/data/generate_context.py \
      --repo vllm-project/vllm \
      --local-repo ~/code/vllm \
      --checkout <baseline_sha> \
      --force

  # 生成 vllm-ascend 架构知识（基于最新 main 代码）
  python src/data/generate_context.py \
      --repo vllm-project/vllm-ascend \
      --local-repo ~/code/vllm-ascend \
      --force

  # 生成交叉引用（基于两个架构文件生成跨项目关系）
  python src/data/generate_context.py \
      --cross-reference \
      --checkout <baseline_sha> \
      --force

执行流程：
  1. 解析参数，确定要处理的仓库和模式（单仓库生成 / 交叉引用）
  2. 如果指定了 --checkout，先 pull 最新代码确保目标 commit 存在，然后临时 checkout
  3. 完成后自动恢复 HEAD
  4. 遍历本地源码目录，构建目录树
  5. 调用 opencode（agent 模式），读取关键接口文件，生成结构化架构摘要
  6. 保存架构摘要到 data/{repo}/context/architecture.json
  7. 如果是 vllm 仓库，重置 arch_deltas.json（新基线，增量清空）
  8. 交叉引用模式：读取两个架构文件，调用 opencode 分析跨项目关系
  9. 将 cross_project_relationship 写入两个架构文件
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data._source_repo import ensure_repo, get_current_sha, repo_dir_name, _find_upstream_remote, KNOWN_REPOS
from data._track_arch_delta import reset_deltas
from data._opencode_client import call_opencode

TZ_CN = timezone(timedelta(hours=8))

# ── Directory names to skip when walking the tree ───────────────────
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "build", "dist", ".egg-info", ".mypy_cache", ".pytest_cache",
    ".hypothesis", ".tox", ".nox", ".direnv",
    ".github", ".buildkite", ".buildifier",
    "csrc",
}

# Key interface files that define the project's abstraction boundaries.
# Reading these gives the AI the core architecture without walking every file.

REPO_SOURCE_DIRS = {
    "vllm-project/vllm": "vllm",
    "vllm-project/vllm-ascend": "vllm_ascend",
}

def build_knowledge_base_template():
    """Build the fixed template for knowledge_base fields.

    These are hardcoded values that do NOT need LLM generation:
    - development_workflows: stable workflows that rarely change
    - testing_guide: test commands and environment setup

    Returns a JSON string for the prompt.
    """
    kb = {
        "development_workflows": {
            "vllm": [
                {
                    "topic": "添加新模型",
                    "steps": [
                        "在 vllm/model_executor/models/ 下创建模型文件",
                        "在 vllm/model_executor/models/registry.py 中注册",
                        "在 vllm/config/model.py 中添加架构默认值（如需要）",
                        "添加测试：tests/models/",
                    ],
                },
                {
                    "topic": "添加配置项",
                    "steps": [
                        "在 vllm/config/ 对应配置类中添加字段（使用 @dataclass + config 元数据）",
                        "在 vllm/engine/arg_utils.py 中添加 CLI 参数（映射到配置字段）",
                        "添加测试",
                    ],
                },
                {
                    "topic": "添加平台后端",
                    "steps": [
                        "在 vllm/platforms/ 中创建新的 Platform 子类",
                        "通过 vllm.platform_plugins entry point 注册",
                        "实现平台特定的 attention、worker、通信等",
                    ],
                },
            ],
            "vllm-ascend": [
                {
                    "topic": "添加 Platform Patch",
                    "steps": [
                        "在 vllm_ascend/patch/platform/ 中创建 patch 文件",
                        "实现 monkey-patch 逻辑（修改上游 vLLM 的类/函数）",
                        "在 vllm_ascend/patch/__init__.py 中添加文档（what/why/how/related PR/future plan）",
                        "Patch 会被 adapt_patch(is_global_patch=True) 自动发现和加载",
                    ],
                },
                {
                    "topic": "添加 Worker Patch",
                    "steps": [
                        "在 vllm_ascend/patch/worker/ 中创建 patch 文件",
                        "实现 monkey-patch 逻辑",
                        "在 vllm_ascend/patch/__init__.py 中添加文档（同上）",
                        "Patch 会被 adapt_patch(is_global_patch=False) 自动发现和加载",
                    ],
                },
                {
                    "topic": "适配新模型到 NPU",
                    "steps": [
                        "评估是否需要 patch：attention 替换 → worker patch；修改 forward → worker patch；结构差异大 → 在 vllm_ascend/models/ 中创建 NPU 特有实现",
                        "添加 worker patch（如 patch_<model_name>.py）",
                        "在 vllm_ascend/patch/__init__.py 中记录",
                        "添加测试：tests/e2e/models/",
                    ],
                },
                {
                    "topic": "添加环境变量",
                    "steps": [
                        "在 vllm_ascend/envs.py 的 env_variables 字典中添加",
                        "命名遵循 VLLM_ASCEND_* 规范",
                        "添加文档注释（默认值、有效范围、是否敏感）",
                        "在代码中通过 from vllm_ascend import envs 引用，禁止硬编码环境变量名",
                    ],
                },
                {
                    "topic": "添加 Attention 后端",
                    "steps": [
                        "在 vllm_ascend/attention/ 中创建新的 attention 实现",
                        "继承 vllm_ascend/attention/abstract.py 中的基类",
                        "在 vllm_ascend/attention/__init__.py 中注册",
                    ],
                },
            ],
        },
        "testing_guide": {
            "vllm": {
                "environment_setup": "cd ~/code/vllm && source .venv/bin/activate && uv pip install -r requirements/test/cuda.txt",
                "test_commands": [
                    ".venv/bin/python -m pytest tests/path/to/test_file.py -v",
                    ".venv/bin/python -m pytest tests/models/ -v",
                ],
                "lint_commands": [
                    "pre-commit run --all-files",
                    "pre-commit run ruff-check --all-files",
                ],
            },
            "vllm-ascend": {
                "environment_setup": "cd ~/code/vllm-ascend && pip install -e .[dev]",
                "test_commands": [
                    "pytest -sv tests/ut/",
                    "pytest -sv tests/e2e/pull_request/one_card/",
                ],
                "lint_commands": [
                    "ruff check vllm_ascend/",
                    "bash format.sh ci",
                ],
            },
        },
    }
    return json.dumps(kb, ensure_ascii=False, indent=2)


def _build_architecture_schema():
    """Build JSON Schema for architecture.json output."""
    return {
        "type": "object",
        "properties": {
            "overview": {"type": "string"},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "key_classes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["path", "name", "description"],
                },
            },
            "key_abstractions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "location": {"type": "string"},
                        "inherits_from": {"type": ["string", "null"]},
                        "key_methods": {"type": "array", "items": {"type": "string"}},
                        "ascend_implementations": {"type": "array", "items": {"type": "string"}},
                        "relationships": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "description", "location"],
                },
            },
            "implementation_principles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "module": {"type": "string"},
                        "problem": {"type": "string"},
                        "workflow": {"type": "string"},
                        "interactions": {"type": "string"},
                        "platform_differences": {"type": "string"},
                    },
                    "required": ["module", "problem", "workflow", "interactions"],
                },
            },
            "module_dependencies": {"type": "string"},
            "hardware_abstraction": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "platform_independent": {"type": "array", "items": {"type": "string"}},
                    "platform_specific": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["description"],
            },
            "interface_surface": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "inheritable_interfaces": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "interface": {"type": "string"},
                                "location": {"type": "string"},
                                "ascend_impl": {"type": "string"},
                                "key_methods": {"type": "array", "items": {"type": "string"}},
                                "impact_rule": {"type": "string"},
                            },
                            "required": ["interface", "location", "ascend_impl"],
                        },
                    },
                    "not_used_by_ascend": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["description"],
            },
            "test_structure": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
            "knowledge_base": {
                "type": "object",
                "properties": {
                    "patch_catalog": {
                        "type": "object",
                        "properties": {
                            "platform_patches": {"type": "array"},
                            "worker_patches": {"type": "array"},
                            "v2_worker_patches": {"type": "array"},
                        },
                    },
                    "development_workflows": {"type": "object"},
                    "testing_guide": {"type": "object"},
                },
            },
        },
        "required": [
            "overview", "modules", "key_abstractions",
            "implementation_principles", "module_dependencies",
            "hardware_abstraction", "interface_surface",
        ],
    }


def _build_cross_ref_schema():
    """Build JSON Schema for cross-reference output."""
    return {
        "type": "object",
        "properties": {
            "vllm_to_ascend_map": {
                "type": "object",
                "additionalProperties": {"type": ["string", "null"]},
            },
            "ascend_only_components": {
                "type": "array",
                "items": {"type": "string"},
            },
            "impact_judgment_rules": {
                "type": "object",
                "properties": {
                    "definitely_affected_paths": {"type": "array", "items": {"type": "string"}},
                    "potentially_affected_paths": {"type": "array", "items": {"type": "string"}},
                    "never_affected_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["definitely_affected_paths", "potentially_affected_paths", "never_affected_paths"],
            },
            "patch_impact_map": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["vllm_to_ascend_map", "ascend_only_components", "impact_judgment_rules", "patch_impact_map"],
    }


CONTEXT_PROMPT_TEMPLATE = """你是一个资深代码架构分析师。请根据以下项目源码结构目录树和关键接口文件内容，生成一份结构化的项目知识摘要。

## 仓库信息
- 仓库：{repo}
- 分支：main
- 分析的 commit：{commit_sha}

## 重要说明：这份架构知识将作为"基线快照"使用
你生成的这份架构知识将作为后续代码变更分析的**基线**。后续每次分析某个 commit 时，会在这个基线的基础上叠加"架构增量变更"来还原该 commit 发生时的代码状态。因此：

- **接口定义必须完整准确**：`interface_surface` 中列出的每个可继承接口，必须包含完整的关键方法签名。后续 commit 分析需要依赖这些信息来判断接口是否被修改。
- **影响规则必须稳定**：`cross_project_relationship` 中的 `definitely_affected_paths` / `never_affected_paths` 会被自动匹配引擎使用。请确保这些路径模式是精确的，不会漏报也不会误报。
- **模块描述要反映 commit 时的代码状态**：代码可能在未来变化，但这份知识锚定的是当前 commit。

## 项目源码目录树
```
{tree}
```

## 关键接口文件
源码位于 {local_repo}，请使用 Read 工具读取关键接口文件来理解项目架构。从目录树中重点关注：
- 平台抽象层（platform/）
- 引擎核心（engine/）
- Worker/Model Runner（worker/）
- Attention 后端（attention/）
- Scheduler 和 KV Cache（core/sched/、kv_cache_interface.py）
- Config（config/）
- Model Executor（model_executor/）
- Compilation（compilation/）
- Sampling（sample/）
- Distributed（distributed/）
- Patch（patch/，仅 vllm-ascend）

## 分析要求
请基于以上信息，分析以下内容：

1. **项目概述**：项目是什么、解决什么问题
2. **核心模块**：列出主要模块/目录及其职责，对于有技术深度的模块（如 Attention、Worker、Compilation、Distributed），请在描述中包含 **实现原理**（这个模块怎么工作、为什么这样设计）
3. **关键抽象**：核心类/接口，要求包含：
   - inherits_from：该类/接口继承自哪个基类（如果是扩展 vllm 的抽象，标注出来）
   - key_methods：列出关键方法及其签名，简要说明作用
   - ascend_implementations：如果 vllm-ascend 实现了此接口，列出对应的 ascend 类名（vllm 仓库时填写）
4. **实现原理**：针对核心模块/技术，描述其实现原理和技术细节，包括：
   - 它解决了什么问题
   - 核心工作流程（用文字描述即可，不要写代码）
   - 与其他模块的交互方式
   - 不同硬件平台的差异处理方式
{extra_context}
5. **模块依赖关系**：模块间如何调用和依赖
6. **硬件适配层**：与硬件相关的抽象层，哪些是平台无关的接口，哪些是平台特定的实现
7. **接口面**（interface_surface）——非常重要：列出所有被外部平台插件（如 vllm-ascend）继承/复写的核心接口：
   - 对每个接口，说明：基类位置、ascend 实现类名、关键方法签名、影响规则（签名/行为变更的后果）
   - 同时列出 **不被 vllm-ascend 使用** 的模块/路径（如 flashinfer、cuda.py、rocm.py 等纯平台特定代码）

## 附加要求：生成 knowledge_base 字段
请在 JSON 输出中增加 `knowledge_base` 字段，包含以下内容：

1. **patch_catalog**: 从 patch/__init__.py 中提取的 patch 信息（如果你能看到该文件内容）。包含 targets（修改的目标类/函数）、why、how、related_pr、future_plan。
2. **development_workflows**: 请参考以下固定模板，嵌入到 knowledge_base 中。这些模板是固定的，不需要修改。
```json
{knowledge_base_template}
```"""

VLLM_EXTRA_CONTEXT = """
8. **与 vllm-ascend 的关系**：
   - 特别关注哪些模块/接口是 vllm-ascend 必须继承或复写的
   - interface_surface 字段需要非常详尽，这是后续 commit 分析判断 ascend_impact 的核心依据
   - not_used_by_ascend 需要包含所有绝对不影响 vllm-ascend 的路径（如纯 CUDA kernel、纯 ROCm 代码、纯 FlashInfer 后端等）
   - 实现原理示例主题：
     * EngineCore 调度循环：如何从 Scheduler 取 batch → Executor 分发到 Worker → 收集结果 → 输出处理
     * GPUModelRunner 前向传播：execute_model() 的完整流程，哪些步骤是可以用子类 override 的
     * Platform 插件加载机制：__init__.py 中的 auto-detect 流程，OOT 平台如何通过 entry_points 注入
     * AttentionBackend 注册与选择：get_attn_backend_cls() 的缓存和 fallback 逻辑
     * torch.compile 集成：CompilerInterface → InductorAdaptor → CUDAGraph 的编译流水线
     * KV Cache 管理：block_pool → scheduler → attention backend 的数据流"""

ASCEND_EXTRA_CONTEXT = """
8. **作为 vLLM 的 Ascend 适配层**：
   - 分析 vllm-ascend 如何扩展 vllm 的每个抽象接口
   - interface_surface 中的 inheritable_interfaces 需要说明基类来自 vLLM 的哪个文件
   - 实现原理示例主题：
     * NPUPlatform 注册流程：从 vllm_ascend/__init__.py register() → vLLM 插件系统 → NPUPlatform 实例化
     * NPUModelRunner 与 GPUModelRunner 的差异：哪些方法被 override、哪些是新增的
     * ACL Graph 机制：与 CUDA Graph 的差异（API 不同、NZ 格式、capture 流程差异）
     * AscendAttentionBackend 的 NZ 格式处理：KV cache shape 差异、get_kv_cache_shape 返回格式
     * Patch 机制：adapt_patch() 的执行时机、platform 级 vs worker 级的区别
     * EPLB 负载均衡：expert 路由权重分配的工作流程
     * CaMem 分配器：与 PyTorch 默认分配器的差异"""

CROSS_REFERENCE_PROMPT = """你是一个资深代码架构分析师。以下是将两个项目的架构摘要合并，请你分析两者之间的继承/复写/依赖关系。

## vllm 架构摘要
```json
{vllm_context_json}
```

## vllm-ascend 架构摘要
```json
{ascend_context_json}
```

## 分析要求
请基于以上两份架构摘要，输出跨项目关系分析。重点关注：

1. **类/接口映射**：vLLM 中的每个 interface_surface.inheritable_interfaces 在 vllm-ascend 中对应的实现类
2. **Ascend 独有组件**：vllm-ascend 中哪些组件没有对应的 vLLM 基类（如 ACLGraphWrapper、CaMemAllocator 等）
3. **影响判断规则**：基于接口面分析，给出一套具体的 ascend_impact 判断规则：
   - 哪些 vLLM 文件/路径的变更 **必然** 影响 vllm-ascend（如 platform/__init__.py、worker_base.py 签名变更）
   - 哪些 vLLM 文件/路径的变更 **可能** 影响 vllm-ascend（如 engine/core.py、config/ 的行为变更）
   - 哪些 vLLM 文件/路径的变更 **绝不** 影响 vllm-ascend（如 flashinfer、cuda.py、rocm.py）
4. **Patch 影响面**：vllm-ascend 通过 patch 机制修改了 vLLM 的哪些模块，这些模块的变更如何影响 ascend

## 输出格式
输出 JSON 格式，不要输出其他内容：
```json
{{
  "vllm_to_ascend_map": {{
    "<vLLM 类全限定名或文件路径>": "<对应 vllm-ascend 类名或文件路径，无实现则标注 null>"
  }},
  "ascend_only_components": [
    "<没有 vLLM 基类的 vllm-ascend 组件>"
  ],
  "impact_judgment_rules": {{
    "definitely_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：为什么必然影响>"
    ],
    "potentially_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：什么条件下会影响>"
    ],
    "never_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：为什么不影响>"
    ]
  }},
  "patch_impact_map": {{
    "<vLLM 被 patch 的模块路径>": "<对应的 vllm-ascend patch 文件>"
  }}
}}
```"""


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return None


def save_json_atomic(filepath, data):
    dirpath = os.path.dirname(filepath)
    os.makedirs(dirpath, exist_ok=True)
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e


def build_tree(local_repo, source_dir, max_depth=4):
    lines = []
    root = os.path.join(local_repo.rstrip("/"), source_dir)

    def walk(dir_path, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return
        dirs = []
        files = []
        for e in entries:
            fp = os.path.join(dir_path, e)
            if os.path.isdir(fp):
                if e not in IGNORE_DIRS and not e.startswith("."):
                    dirs.append(e)
            elif e.endswith(".py"):
                files.append(e)
        indent = "  " * depth
        for d in dirs:
            lines.append(f"{indent}{d}/")
            walk(os.path.join(dir_path, d), depth + 1)
        for f in files:
            lines.append(f"{indent}{f}")

    if os.path.isdir(root):
        walk(root, 0)
    return "\n".join(lines)


def generate_context(repo, data_dir, force, local_repo=None, checkout_sha=None):
    """Phase 1: Generate architecture.json for a single repo using opencode agent."""
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    context_path = os.path.join(repo_dir, "context", "architecture.json")

    if os.path.exists(context_path) and not force:
        existing = load_json(context_path)
        if existing:
            gen_time = existing.get("generated_at", "unknown")
            print(f"Context already exists (generated at {gen_time}), use --force to regenerate")
            return True

    if not local_repo:
        print("Error: local_repo is required for tree walking")
        return False

    # Pull latest before checkout to ensure the target commit exists locally
    if checkout_sha:
        import subprocess
        try:
            upstream = _find_upstream_remote(local_repo, repo)
            branch = "main"
            result = subprocess.run(
                ["git", "pull", "--ff-only", upstream, branch],
                cwd=local_repo, capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                if "Already up to date" in output:
                    print(f"  Repo already up to date")
                else:
                    print(f"  Pulled latest: {output[:100]}")
        except Exception as e:
            print(f"  Warning: pull failed: {e}")

    source_dir = REPO_SOURCE_DIRS.get(repo, repo_dir_name(repo))
    is_vllm = "vllm-ascend" not in repo

    orig_head = None
    try:
        if checkout_sha:
            orig_head = _checkout_and_restore(local_repo, checkout_sha)

        print(f"Building directory tree for {repo} (source: {source_dir})...")
        tree = build_tree(local_repo, source_dir)
        print(f"  -> {len(tree.split(chr(10)))} entries")

        extra = VLLM_EXTRA_CONTEXT if is_vllm else ASCEND_EXTRA_CONTEXT
        commit_sha = get_current_sha(local_repo) or "unknown"

        knowledge_base_template = build_knowledge_base_template()

        prompt = CONTEXT_PROMPT_TEMPLATE.format(
            repo=repo,
            commit_sha=commit_sha,
            local_repo=local_repo,
            tree=tree,
            extra_context=extra,
            knowledge_base_template=knowledge_base_template,
        )

        arch_schema = _build_architecture_schema()

        print("Calling opencode (agent mode) to synthesize architecture summary...")
        context = call_opencode(
            prompt=prompt,
            json_schema=arch_schema,
            add_dirs=[local_repo],
        )
        if context is None:
            print("Failed to get response from opencode")
            return False

        if not isinstance(context, dict):
            print(f"Unexpected response type: {type(context)}")
            return False

        context["repo"] = repo
        context["commit_sha"] = commit_sha
        context["generated_at"] = datetime.now(TZ_CN).isoformat()

        arch_history = context.get("architecture_history", [])
        if arch_history:
            last_entry = arch_history[-1]
            prev_sha = context.get("commit_sha")
            if prev_sha and prev_sha != commit_sha:
                arch_history.append({
                    "commit_sha": commit_sha,
                    "generated_at": context["generated_at"],
                })
        else:
            if commit_sha != "unknown":
                arch_history = [{
                    "commit_sha": commit_sha,
                    "generated_at": context["generated_at"],
                }]
        if arch_history:
            context["architecture_history"] = arch_history

        save_json_atomic(context_path, context)
        print(f"Architecture context saved to {context_path}")

        repo_short = repo_dir_name(repo)
        reset_deltas(data_dir, repo_short, commit_sha, context["generated_at"])
        print(f"  -> Architecture deltas reset (new baseline: {commit_sha[:12]})")

        return True

    finally:
        if orig_head:
            _restore_head(local_repo, orig_head)


def generate_cross_reference(data_dir, force, vllm_local=None, ascend_local=None):
    """Phase 2: Cross-reference vllm and vllm-ascend architectures.

    Reads both architecture.json files, sends them to the LLM along with
    local repo paths (if available) so opencode can read source files
    for more accurate impact judgment rules.
    """
    vllm_dir = os.path.join(data_dir, repo_dir_name("vllm-project/vllm"))
    ascend_dir = os.path.join(data_dir, repo_dir_name("vllm-project/vllm-ascend"))
    vllm_path = os.path.join(vllm_dir, "context", "architecture.json")
    ascend_path = os.path.join(ascend_dir, "context", "architecture.json")

    vllm_ctx = load_json(vllm_path)
    ascend_ctx = load_json(ascend_path)

    if not vllm_ctx:
        print("Error: vllm architecture.json not found. Run phase 1 first.")
        return False
    if not ascend_ctx:
        print("Error: vllm-ascend architecture.json not found. Run phase 1 first.")
        return False

    # Check if cross reference already exists
    if (vllm_ctx.get("cross_project_relationship") and
            ascend_ctx.get("cross_project_relationship") and
            not force):
        print("Cross reference already exists, use --force to regenerate")
        return True

    print("Phase 2: Generating cross-project relationship...")

    # Only send the essential fields, not the full arch.json which is too large
    def slim_ctx(ctx):
        return {
            "repo": ctx.get("repo"),
            "overview": ctx.get("overview"),
            "modules": ctx.get("modules", []),
            "key_abstractions": [
                {
                    "name": a.get("name"),
                    "location": a.get("location"),
                    "inherits_from": a.get("inherits_from"),
                    "key_methods": a.get("key_methods", []),
                    "ascend_implementations": a.get("ascend_implementations", []),
                }
                for a in (ctx.get("key_abstractions") or [])
            ],
            "interface_surface": ctx.get("interface_surface"),
        }

    vllm_json = json.dumps(slim_ctx(vllm_ctx), ensure_ascii=False, indent=2)
    ascend_json = json.dumps(slim_ctx(ascend_ctx), ensure_ascii=False, indent=2)

    prompt = CROSS_REFERENCE_PROMPT.format(
        vllm_context_json=vllm_json,
        ascend_context_json=ascend_json,
    )

    cross_ref_schema = _build_cross_ref_schema()

    print("Calling opencode (agent mode) for cross-reference...")
    cross_ref = call_opencode(
        prompt=prompt,
        json_schema=cross_ref_schema,
        add_dirs=[d for d in [vllm_local, ascend_local] if d],
    )
    if cross_ref is None:
        print("Failed to get cross-reference from opencode")
        return False

    # Write cross_project_relationship into both architecture files
    for ctx, path in [(vllm_ctx, vllm_path), (ascend_ctx, ascend_path)]:
        ctx["cross_project_relationship"] = cross_ref
        ctx["generated_at"] = datetime.now(TZ_CN).isoformat()
        save_json_atomic(path, ctx)
        print(f"Updated cross_project_relationship in {path}")

    return True


def _checkout_and_restore(local_repo, checkout_sha):
    """Temporarily checkout a commit, return orig_head. Caller must restore in finally."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=local_repo, capture_output=True, text=True, timeout=15,
    )
    orig_head = result.stdout.strip()
    print(f"Temporarily checking out {checkout_sha[:12]}...")
    subprocess.run(
        ["git", "checkout", "--force", checkout_sha],
        cwd=local_repo, capture_output=True, text=True, timeout=60,
    )
    return orig_head


def _restore_head(local_repo, orig_head):
    """Restore original HEAD after checkout."""
    import subprocess
    print(f"Restoring to original HEAD ({orig_head[:12]})...")
    subprocess.run(
        ["git", "checkout", "--force", orig_head],
        cwd=local_repo, capture_output=True, text=True, timeout=60,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate project architecture context for AI analysis"
    )
    parser.add_argument(
        "--repo", action="append", default=[],
        help="GitHub repo (owner/repo), can specify multiple times"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force regenerate even if context exists"
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Data directory (default: data)"
    )
    parser.add_argument(
        "--local-repo", default=None,
        help="Path to local repo source code (auto-detected)"
    )
    parser.add_argument(
        "--cross-reference", action="store_true",
        help="Run phase 2: generate cross_project_relationship from existing architecture.json files"
    )
    parser.add_argument(
        "--checkout", default=None,
        help="Temporarily checkout this commit SHA for architecture generation, then restore HEAD"
    )
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.cross_reference:
        # Auto-discover both repos from KNOWN_REPOS
        cross_repos = ["vllm-project/vllm", "vllm-project/vllm-ascend"]
        vllm_local = None
        ascend_local = None
        for repo in cross_repos:
            local = ensure_repo(repo, args.local_repo, project_dir, skip_pull=True)
            if "vllm-ascend" in repo:
                ascend_local = local
            else:
                vllm_local = local

        # Temporarily checkout vllm to baseline if specified
        orig_head = None
        try:
            if args.checkout and vllm_local:
                orig_head = _checkout_and_restore(vllm_local, args.checkout)
            result = generate_cross_reference(args.data_dir, args.force,
                                              vllm_local=vllm_local,
                                              ascend_local=ascend_local)
        finally:
            if orig_head and vllm_local:
                _restore_head(vllm_local, orig_head)
        sys.exit(0 if result else 1)

    if not args.repo:
        print("Error: at least one --repo is required (or use --cross-reference)")
        sys.exit(1)

    success = True
    for repo in args.repo:
        print(f"\n{'='*60}")
        print(f"Processing: {repo}")
        print(f"{'='*60}")
        local = ensure_repo(repo, args.local_repo, project_dir, skip_pull=True)
        if not local:
            print(f"Error: cannot locate local repo for {repo}")
            success = False
            continue
        result = generate_context(repo, args.data_dir, args.force,
                                  local_repo=local, checkout_sha=args.checkout)
        if not result:
            print(f"FAILED: {repo}")
            success = False
        else:
            print(f"DONE: {repo}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
