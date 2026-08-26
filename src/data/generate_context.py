#!/usr/bin/env python3
"""
生成 triton / triton-ascend 项目的架构知识库（architecture.json）。

用法：
  # 生成 triton 架构基线（锚定指定 commit）
  python src/data/generate_context.py \
      --repo triton-lang/triton \
      --local-repo ~/code/triton \
      --checkout <baseline_sha> \
      --force

  # 生成 triton-ascend 架构知识（基于最新 main 代码）
  python src/data/generate_context.py \
      --repo triton-lang/triton-ascend \
      --local-repo ~/code/triton-ascend \
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
  7. 如果是 triton 仓库，重置 arch_deltas.json（新基线，增量清空）
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
from data._extract_patches import build_patch_catalog

TZ_CN = timezone(timedelta(hours=8))

# ── Directory names to skip when walking the tree ───────────────────
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "build", "dist", ".egg-info", ".mypy_cache", ".pytest_cache",
    ".hypothesis", ".tox", ".nox", ".direnv",
    ".github", ".buildkite", ".buildifier",
    "csrc",
}

# Source file extensions listed in the directory tree. triton's compiler core
# (include/, lib/Dialect/, lib/Conversion/) is C++/MLIR, so a Python-only tree
# would render exactly the directories the prompt asks the agent to focus on as
# empty.
SOURCE_EXTS = (".py", ".h", ".cpp", ".td", ".cc")

# Key interface files that define the project's abstraction boundaries.
# Reading these gives the AI the core architecture without walking every file.

REPO_SOURCE_DIRS = {
    "triton-lang/triton": ".",
    "triton-lang/triton-ascend": ".",
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
            "triton": [
                {
                    "topic": "添加新算子/语言特性",
                    "steps": [
                        "在 python/triton/language/ 中定义 Python API（语义约定 + 类型签名）",
                        "在 include/ 中声明 IR op，在 lib/Dialect/Triton*/ 中实现（builder、verifier、canonicalization）",
                        "必要时在 python/src/ 和 lib/Conversion/ 中添加 lowering pass",
                        "添加 lit 测试：test/ 下对应目录",
                    ],
                },
                {
                    "topic": "添加编译器 pass",
                    "steps": [
                        "在 lib/Dialect/Triton*/Transforms/ 中实现 pass（Passes.td 中注册）",
                        "在 python/triton/compiler/ 的编译流水线中插入",
                        "添加 lit 测试验证 IR 变换",
                    ],
                },
                {
                    "topic": "添加新硬件后端",
                    "steps": [
                        "在 python/triton/backends/ 下实现 BaseBackend/DriverBase 子类",
                        "通过 importlib.metadata entry_points 注册（backends/__init__.py 中的 _find_concrete_subclasses 发现机制）",
                        "第三方后端代码放在 third_party/<vendor>/",
                        "实现 lib/ 中的 target 相关 lowering",
                    ],
                },
            ],
            "triton-ascend": [
                {
                    "topic": "Ascend 后端开发",
                    "steps": [
                        "在 third_party/ascend/backend/ 中实现 AscendBackend（compiler.py）、AscendDriver（driver.py）",
                        "通过 third_party/ascend/backend/backend_register.py 完成注册",
                        "NPU 相关 lowering/优化在 third_party/ascend/lib/、third_party/ascend/language/ 中",
                        "添加测试：third_party/ascend/unittest/ 或 unittest/",
                    ],
                },
                {
                    "topic": "回合上游 triton 代码",
                    "steps": [
                        "人工 cherry-pick 上游 commit（目前无 main2main 同步机制）",
                        "建议在 commit message 或 PR 描述中记录对应的上游 SHA/PR（便于后续跟踪适配状态）",
                        "冲突较大时，在 third_party/ascend/patch/ 中维护整体 patch 文件（triton-ascend-<version>.patch）",
                        "回合后运行 Ascend CI 验证",
                    ],
                },
                {
                    "topic": "添加算子支持",
                    "steps": [
                        "在 third_party/ascend/language/ 中扩展 DSL 支持（如需要 Ascend 特有语义）",
                        "或复用上游 python/triton/language/ API，在 third_party/ascend/lib/ 中实现 lowering",
                        "costmodel 调优：third_party/ascend/costmodel/",
                        "添加单测和算子示例",
                    ],
                },
                {
                    "topic": "维护 patch 文件",
                    "steps": [
                        "third_party/ascend/patch/ 下为整体式 .patch 文件（triton-ascend-<version>.patch、dev 版本、llvm_patch_<sha>.patch）",
                        "版本发布时更新 release patch；日常开发维护 dev patch",
                        "patch 文件由构建流程应用（setup_ascend.py / CMakeLists.txt 引用）",
                    ],
                },
            ],
        },
        "testing_guide": {
            "triton": {
                "environment_setup": "cd ~/code/triton && pip install -e python（需要 CUDA 环境）",
                "test_commands": [
                    "pytest python/test/ -v",
                    "cd python && pytest -v test/language/",
                ],
                "lint_commands": [
                    "pre-commit run --all-files",
                ],
            },
            "triton-ascend": {
                "environment_setup": "需要 CANN 环境 + torch_npu；cd ~/code/triton-ascend && pip install -e .（详见 README_zh.md 安装章节）",
                "test_commands": [
                    "pytest -sv unittest/",
                    "pytest -sv third_party/ascend/unittest/",
                ],
                "lint_commands": [
                    "pre-commit run --all-files",
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
            "triton_to_ascend_map": {
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
        "required": ["triton_to_ascend_map", "ascend_only_components", "impact_judgment_rules", "patch_impact_map"],
    }


CONTEXT_PROMPT_TEMPLATE = """你是一个资深代码架构分析师。请根据以下项目源码结构目录树和关键接口文件内容，生成一份结构化的项目知识摘要。

## 仓库信息
- 仓库：{repo}
- 分支：main
- 分析的 commit：{commit_sha}

## 输出 JSON 的字段名规范（必须逐字精确使用）
顶层对象必须包含以下键，键名必须与下面列出的完全一致，不得翻译、缩写或自创（如 project_overview、core_modules、hardware_adaptation、snapshot 等都是错误键名）：

- `overview`：字符串，项目概述
- `modules`：数组，每个元素为对象，键为 `path`（字符串）、`name`（字符串）、`description`（字符串）、`key_classes`（字符串数组）
- `key_abstractions`：数组，每个元素为对象，键为 `name`、`description`、`location`、`inherits_from`（字符串或 null）、`key_methods`（字符串数组）、`ascend_implementations`（字符串数组）、`relationships`（字符串数组）
- `implementation_principles`：数组，每个元素为对象，键为 `module`、`problem`、`workflow`、`interactions`、`platform_differences`
- `module_dependencies`：字符串
- `hardware_abstraction`：对象，键为 `description`（字符串）、`platform_independent`（字符串数组）、`platform_specific`（字符串数组）
- `interface_surface`：对象，键为 `description`（字符串）、`inheritable_interfaces`（数组，每个元素为对象：`interface`、`location`、`ascend_impl`、`key_methods`、`impact_rule`）、`not_used_by_ascend`（字符串数组）
- `test_structure`：对象，键为 `path`（字符串）、`description`（字符串）
- `knowledge_base`：对象，键为 `patch_catalog`、`development_workflows`、`testing_guide`

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
- 前端语言层（python/triton/language/、python/triton/experimental/）
- 编译器流水线（python/triton/compiler/）
- 后端抽象（python/triton/backends/：BaseBackend、DriverBase、entry_points 注册）
- Runtime（python/triton/runtime/、python/triton/_C/）
- IR/编译器实现（include/、lib/Dialect/Triton*/、lib/Conversion/）
- Python 绑定（python/src/）
- 第三方后端（third_party/amd、third_party/nvidia；triton-ascend 为 third_party/ascend/）
- Patch（third_party/ascend/patch/，仅 triton-ascend）

## 分析要求
请基于以上信息，分析以下内容：

1. **项目概述**：项目是什么、解决什么问题
2. **核心模块**：列出主要模块/目录及其职责，对于有技术深度的模块（如 Compiler、Backends、Language frontend、Runtime），请在描述中包含 **实现原理**（这个模块怎么工作、为什么这样设计）
3. **关键抽象**：核心类/接口，要求包含：
   - inherits_from：该类/接口继承自哪个基类（如果是扩展 triton 的抽象，标注出来）
   - key_methods：列出关键方法及其签名，简要说明作用
   - ascend_implementations：如果 triton-ascend 实现了此接口，列出对应的 ascend 类名（triton 仓库时填写）
4. **实现原理**：针对核心模块/技术，描述其实现原理和技术细节，包括：
   - 它解决了什么问题
   - 核心工作流程（用文字描述即可，不要写代码）
   - 与其他模块的交互方式
   - 不同硬件平台的差异处理方式
{extra_context}
5. **模块依赖关系**：模块间如何调用和依赖
6. **硬件适配层**：与硬件相关的抽象层，哪些是平台无关的接口，哪些是平台特定的实现
7. **接口面**（interface_surface）——非常重要：列出所有被外部平台后端（如 triton-ascend）继承/复写的核心接口：
   - 对每个接口，说明：基类位置、ascend 实现类名、关键方法签名、影响规则（签名/行为变更的后果）
   - 同时列出 **不被 triton-ascend 使用** 的模块/路径（如纯 NVIDIA/AMD 特定代码、纯 CUDA kernel 等）

## 附加要求：生成 knowledge_base 字段
请在 JSON 输出中增加 `knowledge_base` 字段，包含以下内容：

1. **patch_catalog**: 从 third_party/ascend/patch/ 目录中的 .patch 文件提取的信息（如果你能看到该目录）。**只包含 main 分支构建实际应用到 triton 源码的 release patch（即 triton-ascend-<版本>.patch）；llvm_patch_*.patch 修改的是 LLVM 依赖而非 triton 源码；triton-ascend-dev-*.patch 只在 dev 构建（main-dev 分支）应用，本分析基于 main，二者都不要包含**。格式必须为对象：{{"patch_files": [{{"name": "<patch 文件名>", "version": "<版本（如有）>", "purpose": "<用途说明>"}}]}}——**注意：size_bytes / total_added / total_removed / target_files 会由确定性 diffstat 解析自动填充，不要自己估算或编造这些字段**（上游 triton 仓库没有该目录时，patch_files 为空数组并附 note 说明）。
2. **development_workflows**: 请参考以下固定模板，嵌入到 knowledge_base 中。这些模板是固定的，不需要修改。
```json
{knowledge_base_template}
```"""

TRITON_EXTRA_CONTEXT = """
8. **与 triton-ascend 的关系**：
   - 特别关注哪些模块/接口是 triton-ascend 必须继承或复写的
   - interface_surface 字段需要非常详尽，这是后续 commit 分析判断 ascend_impact 的核心依据
   - not_used_by_ascend 需要包含所有绝对不影响 triton-ascend 的路径（如纯 NVIDIA/AMD 后端代码、纯 CUDA kernel、特定平台 tools 等）
   - 实现原理示例主题：
     * 编译流水线：frontend AST → Triton IR → TritonGPU IR → LLVM/NPU IR 的 pass 顺序
     * 后端注册机制：python/triton/backends/__init__.py 的 entry_points 发现、BaseBackend/DriverBase 接口
     * Language frontend：@jit 装饰器、kernel 参数语义分析、JITFunction 缓存
     * Runtime 启动流程：launch kernel 的 driver 交互、缓存命中路径
     * IR 结构：include/ 中的 op 定义、lib/Dialect/Triton*/ 的 builder/verifier 分工
     * 第三方后端扩展点：third_party/ 下各后端如何接入编译流水线"""

ASCEND_EXTRA_CONTEXT = """
8. **作为 triton 的 Ascend 后端实现**：
   - 分析 triton-ascend 如何扩展 triton 的每个抽象接口（BaseBackend、DriverBase 等）
   - interface_surface 中的 inheritable_interfaces 需要说明基类来自 triton 的哪个文件
   - 实现原理示例主题：
     * AscendBackend 注册流程：third_party/ascend/backend/backend_register.py → entry_points → _find_concrete_subclasses 实例化
     * AscendDriver 与 DriverBase 的差异：NPU 设备管理、内存分配、kernel launch 的 CANN API 映射
     * Ascend 编译流水线：TritonGPU IR → AscendNPU IR（AscendNPU-IR）→ CANN 的 lowering 过程
     * 上游代码回合方式：人工 cherry-pick、third_party/ascend/patch/*.patch 的维护时机
     * Ascend costmodel：third_party/ascend/costmodel/ 的调优策略
     * 与 CUDA 后端的架构差异：NZ 格式、Ascend IR 特殊 pass、CANN 算子库对接"""

CROSS_REFERENCE_PROMPT = """你是一个资深代码架构分析师。以下是将两个项目的架构摘要合并，请你分析两者之间的继承/复写/依赖关系。

## triton 架构摘要
```json
{triton_context_json}
```

## triton-ascend 架构摘要
```json
{ascend_context_json}
```

## 分析要求
请基于以上两份架构摘要，输出跨项目关系分析。重点关注：

1. **类/接口映射**：triton 中的每个 interface_surface.inheritable_interfaces 在 triton-ascend 中对应的实现类
2. **Ascend 独有组件**：triton-ascend 中哪些组件没有对应的 triton 基类（如 AscendNPU-IR 相关、CANN 对接层等）
3. **影响判断规则**：基于接口面分析，给出一套具体的 ascend_impact 判断规则：
   - 哪些 triton 文件/路径的变更 **必然** 影响 triton-ascend（如 python/triton/backends/ 接口签名变更、IR op 定义变更）
   - 哪些 triton 文件/路径的变更 **可能** 影响 triton-ascend（如编译流水线、language frontend 的行为变更）
   - 哪些 triton 文件/路径的变更 **绝不** 影响 triton-ascend（如 third_party/nvidia、third_party/amd 等纯平台特定代码）
4. **Patch 影响面**：triton-ascend 通过 third_party/ascend/patch/*.patch 修改了 triton 的哪些模块，这些模块的变更如何影响 ascend。只考虑 main 分支构建实际应用的 release patch（triton-ascend-<版本>.patch）；llvm_patch_*.patch 修改的是 LLVM 依赖，triton-ascend-dev-*.patch 只在 dev 构建（main-dev 分支）应用，都不要纳入

## 输出格式
输出 JSON 格式，不要输出其他内容：
```json
{{
  "triton_to_ascend_map": {{
    "<triton 类全限定名或文件路径>": "<对应 triton-ascend 类名或文件路径，无实现则标注 null>"
  }},
  "ascend_only_components": [
    "<没有 triton 基类的 triton-ascend 组件>"
  ],
  "impact_judgment_rules": {{
    "definitely_affected_paths": [
      "<triton 文件/路径模式>",
      "<说明：为什么必然影响>"
    ],
    "potentially_affected_paths": [
      "<triton 文件/路径模式>",
      "<说明：什么条件下会影响>"
    ],
    "never_affected_paths": [
      "<triton 文件/路径模式>",
      "<说明：为什么不影响>"
    ]
  }},
  "patch_impact_map": {{
    "<triton 被 patch 的模块路径>": "<对应的 triton-ascend patch 文件>"
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
            elif e.endswith(SOURCE_EXTS):
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


def _validate_arch_context(context):
    """Check the LLM output against the field names the consumers expect.

    opencode's json_schema is only a soft hint, so the model may invent its
    own top-level keys (e.g. project_overview / core_modules / hardware_
    adaptation). Downstream (analyze_commits triage, MCP tools) reads exact
    names, so fail loudly instead of saving a silently unusable file.
    """
    problems = []
    schema = _build_architecture_schema()
    for key in schema.get("required", []):
        if key not in context:
            problems.append(f"missing top-level key: {key}")
    for key, expect_type in [
        ("overview", str),
        ("modules", list),
        ("key_abstractions", list),
        ("implementation_principles", list),
        ("module_dependencies", str),
        ("hardware_abstraction", dict),
        ("interface_surface", dict),
    ]:
        if key in context and not isinstance(context[key], expect_type):
            problems.append(f"{key}: expected {expect_type.__name__}, got {type(context[key]).__name__}")
    for key in ("modules", "key_abstractions", "implementation_principles"):
        items = context.get(key)
        if isinstance(items, list):
            bad = sum(1 for x in items if not isinstance(x, dict))
            if bad:
                problems.append(f"{key}: {bad}/{len(items)} elements are not objects")
    iface = context.get("interface_surface", {})
    if isinstance(iface, dict):
        for sub in ("inheritable_interfaces", "not_used_by_ascend"):
            if sub not in iface:
                problems.append(f"interface_surface.{sub} missing")
        items = iface.get("inheritable_interfaces")
        if isinstance(items, list) and any(not isinstance(x, dict) for x in items):
            problems.append("interface_surface.inheritable_interfaces: elements must be objects")
    ha = context.get("hardware_abstraction", {})
    if isinstance(ha, dict):
        for sub in ("platform_independent", "platform_specific"):
            if sub not in ha:
                problems.append(f"hardware_abstraction.{sub} missing")
    kb = context.get("knowledge_base", {})
    if isinstance(kb, dict):
        for sub in ("development_workflows", "testing_guide"):
            if sub not in kb:
                problems.append(f"knowledge_base.{sub} missing")
        catalog = kb.get("patch_catalog")
        if catalog is not None:
            if not isinstance(catalog, dict) or not isinstance(catalog.get("patch_files"), list):
                problems.append("knowledge_base.patch_catalog: expected {patch_files: [...]}")
    return problems


def _normalize_patch_catalog(context):
    """Drop patches that do not touch the analyzed triton tree.

    The prompt constrains patch_catalog to {patch_files: [...]}; shape errors
    are caught by _validate_arch_context (which triggers a retry), so this
    function only filters entries:
      - llvm_patch_*.patch patch the LLVM dependency, not triton source.
        Upstream triton commits can never touch those files.
      - triton-ascend-dev-*.patch applies only in dev builds (main-dev /
        version.txt containing "dev", see setup_ascend.py:_is_dev_mode).
        The pipeline analyzes main, so dev patches are noise here too.
    """
    kb = context.get("knowledge_base")
    if not isinstance(kb, dict):
        return
    catalog = kb.get("patch_catalog")
    if not isinstance(catalog, dict):
        return
    raw_items = catalog.get("patch_files")
    if not isinstance(raw_items, list):
        return

    patch_files = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        if name.startswith(("llvm_patch_", "triton-ascend-dev-")):
            continue
        patch_files.append(item)
    result = {"patch_files": patch_files}
    # Preserve an explanatory note if the model provided one (e.g. the
    # upstream repo legitimately has no patch directory).
    if isinstance(catalog.get("note"), str):
        result["note"] = catalog["note"]
    kb["patch_catalog"] = result


def _merge_deterministic_patch_catalog(context, local_repo, repo):
    """Fill patch_catalog numeric fields from deterministic diffstat parsing.

    The LLM provides name/purpose/version, but its numeric fields (size_bytes,
    total_added, total_removed) and target_files came back as nulls/strings in
    practice. _extract_patches.parse_patch_diffstat parses the actual .patch
    files deterministically (verified against `git apply --stat`), so merge by
    patch file name and let deterministic data win for those fields.
    """
    if "triton-ascend" not in repo:
        return
    kb = context.get("knowledge_base")
    if not isinstance(kb, dict):
        return
    catalog = kb.get("patch_catalog")
    if not isinstance(catalog, dict):
        return
    llm_entries = catalog.get("patch_files")
    if not isinstance(llm_entries, list):
        return
    try:
        extracted = build_patch_catalog(local_repo)
    except Exception as e:
        print(f"  [patch] deterministic extraction failed: {e}")
        return
    det_by_name = {p["name"]: p for p in extracted.get("patch_files", [])}
    for entry in llm_entries:
        det = det_by_name.get(entry.get("name"))
        if det:
            entry["size_bytes"] = det["size_bytes"]
            entry["total_added"] = det["total_added"]
            entry["total_removed"] = det["total_removed"]
            entry["target_files"] = det["target_files"]


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
    is_upstream = "triton-ascend" not in repo

    orig_head = None
    try:
        if checkout_sha:
            orig_head = _checkout_and_restore(local_repo, checkout_sha)

        print(f"Building directory tree for {repo} (source: {source_dir})...")
        tree = build_tree(local_repo, source_dir)
        print(f"  -> {len(tree.split(chr(10)))} entries")

        extra = TRITON_EXTRA_CONTEXT if is_upstream else ASCEND_EXTRA_CONTEXT
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
            # Usually means the JSON code fence was truncated or never closed,
            # so _extract_fenced_json fell back to returning the raw text.
            print(f"Unexpected response type: {type(context)} (truncated JSON?)")
            print("  Retrying once with corrective instructions...")
            context = call_opencode(
                prompt=(
                    "你上一次的 JSON 输出不完整（缺少结尾），无法解析。请重新输出"
                    "完整的架构知识 JSON，用 ```json 代码块包裹并保证闭合。顶层字段名"
                    "必须逐字使用规范中的键名（overview、modules、key_abstractions、"
                    "implementation_principles、module_dependencies、hardware_"
                    "abstraction、interface_surface、test_structure、knowledge_base），"
                    "不要自创或翻译键名。"
                ),
                json_schema=arch_schema,
                add_dirs=[local_repo],
            )
            if context is None:
                print("Failed to get retry response from opencode")
                return False
            if not isinstance(context, dict):
                print(f"Unexpected retry response type: {type(context)}")
                return False

        _normalize_patch_catalog(context)
        _merge_deterministic_patch_catalog(context, local_repo, repo)

        problems = _validate_arch_context(context)
        if problems:
            print("  [validate] LLM output does not match the expected schema:")
            for p in problems:
                print(f"    - {p}")
            print("  Retrying once with corrective instructions...")
            retry_prompt = (
                f"你上一次输出的 JSON 字段名不符合要求。存在的问题：\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n\n请重新输出完整的架构知识 JSON。顶层字段名必须逐字使用规范中的键名"
                "（overview、modules、key_abstractions、implementation_principles、"
                "module_dependencies、hardware_abstraction、interface_surface、"
                "test_structure、knowledge_base），不要自创或翻译键名。"
            )
            context = call_opencode(
                prompt=retry_prompt,
                json_schema=arch_schema,
                add_dirs=[local_repo],
            )
            if context is None:
                print("Failed to get retry response from opencode")
                return False
            if not isinstance(context, dict):
                print(f"Unexpected retry response type: {type(context)}")
                return False
            problems = _validate_arch_context(context)
            if problems:
                print("  [validate] Retry output still does not match schema:")
                for p in problems:
                    print(f"    - {p}")
                print("  Aborting: fix the prompt and re-run.")
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


def generate_cross_reference(data_dir, force, triton_local=None, ascend_local=None):
    """Phase 2: Cross-reference triton and triton-ascend architectures.

    Reads both architecture.json files, sends them to the LLM along with
    local repo paths (if available) so opencode can read source files
    for more accurate impact judgment rules.
    """
    triton_dir = os.path.join(data_dir, repo_dir_name("triton-lang/triton"))
    ascend_dir = os.path.join(data_dir, repo_dir_name("triton-lang/triton-ascend"))
    triton_path = os.path.join(triton_dir, "context", "architecture.json")
    ascend_path = os.path.join(ascend_dir, "context", "architecture.json")

    triton_ctx = load_json(triton_path)
    ascend_ctx = load_json(ascend_path)

    if not triton_ctx:
        print("Error: triton architecture.json not found. Run phase 1 first.")
        return False
    if not ascend_ctx:
        print("Error: triton-ascend architecture.json not found. Run phase 1 first.")
        return False

    # Check if cross reference already exists
    if (triton_ctx.get("cross_project_relationship") and
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

    triton_json = json.dumps(slim_ctx(triton_ctx), ensure_ascii=False, indent=2)
    ascend_json = json.dumps(slim_ctx(ascend_ctx), ensure_ascii=False, indent=2)

    prompt = CROSS_REFERENCE_PROMPT.format(
        triton_context_json=triton_json,
        ascend_context_json=ascend_json,
    )

    cross_ref_schema = _build_cross_ref_schema()

    print("Calling opencode (agent mode) for cross-reference...")
    cross_ref = call_opencode(
        prompt=prompt,
        json_schema=cross_ref_schema,
        add_dirs=[d for d in [triton_local, ascend_local] if d],
    )
    if cross_ref is None:
        print("Failed to get cross-reference from opencode")
        return False

    # Write cross_project_relationship into both architecture files
    for ctx, path in [(triton_ctx, triton_path), (ascend_ctx, ascend_path)]:
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

    # 仓库根目录（src/data/X.py 往上三级），clone 兜底落到 <root>/repos/ 而非 src/repos/
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if args.cross_reference:
        # Auto-discover both repos from KNOWN_REPOS
        cross_repos = ["triton-lang/triton", "triton-lang/triton-ascend"]
        triton_local = None
        ascend_local = None
        for repo in cross_repos:
            local = ensure_repo(repo, args.local_repo, project_dir, skip_pull=True)
            if "triton-ascend" in repo:
                ascend_local = local
            else:
                triton_local = local

        # Temporarily checkout triton to baseline if specified
        orig_head = None
        try:
            if args.checkout and triton_local:
                orig_head = _checkout_and_restore(triton_local, args.checkout)
            result = generate_cross_reference(args.data_dir, args.force,
                                              triton_local=triton_local,
                                              ascend_local=ascend_local)
        finally:
            if orig_head and triton_local:
                _restore_head(triton_local, orig_head)
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
