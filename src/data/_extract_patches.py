#!/usr/bin/env python3
"""
Extract patch catalog from vllm-ascend's patch/__init__.py.

The patch/__init__.py file (~1221 lines) contains all patch definitions
with structured documentation. This script parses it to extract:

  - Platform patches (loaded before worker start)
  - Worker patches (loaded per worker)
  - V2 worker patches (new model runner)

Output: structured JSON suitable for arch.json's knowledge_base.patch_catalog.

This does NOT use an LLM — it uses regex-based parsing of the documented
patch entries. The output is deterministic.
"""
import argparse
import json
import os
import re
import sys


def load_patch_init(ascend_repo_path):
    """Load the patch/__init__.py file from the ascend repo."""
    patch_init = os.path.join(ascend_repo_path, "vllm_ascend", "patch", "__init__.py")
    if not os.path.exists(patch_init):
        print(f"Error: {patch_init} not found")
        sys.exit(1)
    with open(patch_init, "r", encoding="utf-8") as f:
        return f.read()


def parse_patches(content):
    """Parse patches from the patch/__init__.py content.

    The file documents patches with a structured format:

      # ** N. File: platform/patch_xxx.py **
      # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
      #   1. `target_class_or_function`
      #    Why:
      #       ...
      #    How:
      #       ...
      #    Related PR (if no, explain why):
      #       ...
      #    Future Plan:
      #       ...

    Same format for worker/patches. Returns a dict with:
      platform_patches, worker_patches, v2_worker_patches
    """
    lines = content.split('\n')

    # Pattern to match patch entry headers:
    #   # ** N. File: platform/patch_xxx.py **
    #   # ** N. File: worker/patch_xxx.py **
    #   # ** N. File: worker/patch_v2/patch_xxx.py **
    header_pattern = re.compile(
        r'#\s*\*{2}\s*\d+\.\s*File:\s*(platform|worker)/(patch_\S+\.py)\s*\*{2}'
    )

    # Locate all patch entries
    sections = []  # (type, filename, start_line, end_line)
    for i, line in enumerate(lines):
        m = header_pattern.search(line)
        if m:
            ptype = m.group(1)  # "platform" or "worker"
            fname = m.group(2)  # e.g. "patch_balance_schedule.py"
            if ptype == "platform":
                entry_type = "platform"
            elif fname.startswith("patch_v2/"):
                entry_type = "v2_worker"
            else:
                entry_type = "worker"
            sections.append((entry_type, fname, i))

    # Extract the text between entries
    entries = []
    for idx, (entry_type, fname, start) in enumerate(sections):
        end = sections[idx + 1][2] if idx + 1 < len(sections) else len(lines)
        entry_lines = lines[start:end]
        parsed = parse_single_patch(entry_lines)
        if parsed:
            parsed["name"] = fname
            entries.append((entry_type, parsed))

    result = {
        "platform_patches": [],
        "worker_patches": [],
        "v2_worker_patches": [],
    }
    for entry_type, parsed in entries:
        result[f"{entry_type}_patches"].append(parsed)

    return result


def parse_single_patch(lines):
    """Parse a single patch entry's comment lines.

    Expected fields in the comment block:
      Why: ...
      How: ...
      Related PR: ...
      Future Plan: ...

    Also extracts the target(s) from lines like:
      #   1. `target_class_or_function`
    """
    info = {
        "targets": [],
        "why": "",
        "how": "",
        "related_pr": "",
        "future_plan": "",
    }

    # Collect all comment text, stripping '# ' prefix
    comment_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            comment_lines.append(stripped.lstrip("#").strip())

    # Parse targets (lines like: 1. `target` or 1. `target1`, `target2`)
    targets = []
    target_pattern = re.compile(r'^\s*\d+\.\s*`([^`]+)`')
    in_targets = True
    for cl in comment_lines:
        m = target_pattern.search(cl)
        if m:
            targets.append(m.group(1))
            in_targets = True
        elif in_targets and cl and not cl.startswith("Why:") and not cl.startswith("How:") and not cl.startswith("Related") and not cl.startswith("Future"):
            # Could be continuation of a multi-line target
            m2 = re.search(r'`([^`]+)`', cl)
            if m2 and not cl.startswith(" "):
                targets.append(m2.group(1))
        elif cl.startswith("Why:") or cl.startswith("How:") or cl.startswith("Related") or cl.startswith("Future"):
            in_targets = False

    info["targets"] = targets

    # Parse field values
    current_field = None
    for cl in comment_lines:
        if cl.startswith("Why:"):
            current_field = "why"
            info["why"] = cl[4:].strip()
        elif cl.startswith("How"):
            current_field = "how"
            # "How：" vs "How:" — handle both
            val = cl[cl.index(":") + 1:].strip() if ":" in cl else ""
            info["how"] = val
        elif cl.lower().startswith("related pr"):
            current_field = "related_pr"
            idx = cl.index(":")
            info["related_pr"] = cl[idx + 1:].strip()
        elif cl.lower().startswith("future plan"):
            current_field = "future_plan"
            idx = cl.index(":")
            info["future_plan"] = cl[idx + 1:].strip()
        elif current_field and cl and not cl.startswith("Why:") and not cl.startswith("How:") and not cl.lower().startswith("related pr") and not cl.lower().startswith("future plan"):
            # Continuation of current field
            info[current_field] += " " + cl.strip()

    # Clean up
    for key in info:
        if isinstance(info[key], str):
            info[key] = info[key].strip()

    return info


def build_patch_catalog(ascend_repo_path):
    """Build the full patch catalog and return a structured dict."""
    content = load_patch_init(ascend_repo_path)
    patches = parse_patches(content)
    return patches


def main():
    parser = argparse.ArgumentParser(
        description="Extract patch catalog from vllm-ascend patch/__init__.py"
    )
    parser.add_argument(
        "--ascend-repo-path", required=True,
        help="Path to vllm-ascend repository"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output JSON file (default: print to stdout)"
    )
    args = parser.parse_args()

    catalog = build_patch_catalog(args.ascend_repo_path)

    output = json.dumps(catalog, ensure_ascii=False, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Patch catalog saved to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()