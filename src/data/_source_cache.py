"""
Source file context cache for deep analysis.
Uses AST parsing to extract public interfaces, no LLM calls needed.
"""

import ast
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))


def extract_file_interfaces(filepath):
    with open(filepath, "r") as f:
        content = f.read()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {"interface_summary": "无法解析", "key_interfaces": []}

    classes = []
    functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({"name": node.name, "methods": methods})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

    summary_parts = []
    if classes:
        summary_parts.append(f"定义 {len(classes)} 个类")
    if functions:
        summary_parts.append(f"定义 {len(functions)} 个函数")
    if not classes and not functions:
        summary_parts.append("无公开类或函数定义")
    interface_summary = "；".join(summary_parts)

    key_interfaces = [c["name"] for c in classes]
    for cls in classes:
        for m in cls["methods"][:5]:
            key_interfaces.append(f"{cls['name']}.{m}")

    return {
        "interface_summary": interface_summary,
        "key_interfaces": key_interfaces,
    }


class SourceContextCache:
    def __init__(self, cache_dir: str, repo_dir: str | None = None):
        self.cache_dir = os.path.join(cache_dir, "_deep_analysis_cache", "source_context")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._base_dir = repo_dir

    def _rel_path(self, filepath: str) -> str:
        if self._base_dir:
            return os.path.relpath(filepath, self._base_dir)
        return os.path.relpath(filepath)

    def _cache_key(self, filepath: str) -> tuple[str | None, str | None]:
        if not os.path.isfile(filepath):
            return None, None
        with open(filepath, "rb") as f:
            content = f.read()
        content_hash = hashlib.sha256(content).hexdigest()[:16]
        safe_name = self._rel_path(filepath).replace("/", "__").replace("\\", "__")
        return safe_name, content_hash

    def get(self, filepath: str) -> dict | None:
        safe_name, content_hash = self._cache_key(filepath)
        if safe_name is None:
            return None
        cache_path = os.path.join(self.cache_dir, f"{safe_name}.json")
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            if cached.get("content_hash") == content_hash:
                return cached.get("analysis")
        except (json.JSONDecodeError, IOError):
            pass
        return None

    def put(self, filepath: str, analysis: dict):
        safe_name, content_hash = self._cache_key(filepath)
        if safe_name is None:
            return
        cache_path = os.path.join(self.cache_dir, f"{safe_name}.json")
        data = {
            "content_hash": content_hash,
            "analysis": analysis,
            "cached_at": datetime.now(TZ_CN).isoformat(),
        }
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cache_path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def ensure_cached(self, filepath: str) -> dict | None:
        cached = self.get(filepath)
        if cached:
            return cached
        analysis = extract_file_interfaces(filepath)
        self.put(filepath, analysis)
        return analysis

    def get_batch_summary(self, filepaths: list[str]) -> str:
        parts = []
        for fp in filepaths:
            cached = self.ensure_cached(fp)
            if cached:
                summary = cached.get("interface_summary", "")
                if summary:
                    rel = self._rel_path(fp)
                    parts.append(f"  {rel}: {summary}")
        if not parts:
            return ""
        return "\n## Cached Source File Analysis\n" + "\n".join(parts)