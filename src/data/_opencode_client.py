#!/usr/bin/env python3
"""
opencode CLI wrapper.

Replaces the former Claude Code client. Uses the `opencode run` CLI as an
agent (it can read source files), driving it through an OpenAI-compatible
provider/model configured in the opencode config (opencode.json).

Environment variables (mirror the old CLAUDE_* but prefixed OPENCODE_):
  OPENCODE_BIN               path to the `opencode` binary (default: discover via PATH)
  OPENCODE_MODEL             provider/model selector for `-m`, e.g. "volcengine-plan/ark-code-latest"
  OPENCODE_AUTH_TOKEN        API key for the provider (used by CI's generated opencode.json)
  OPENCODE_SMALL_FAST_MODEL  (optional) small/fast model name, informational
  OPENCODE_TIMEOUT           per-call timeout in seconds (default 600)
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or "/usr/local/bin/opencode"
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "")
OPENCODE_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "600"))

# Regex-less fence detection: the model is asked to wrap its JSON answer in a
# ```json ... ``` code fence. We scan lines for that fence.
_FENCE_OPEN = "```json"
_FENCE_CLOSE = "```"


def _enqueue_output(stream, line_queue, done_event):
    for line in iter(stream.readline, ""):
        line_queue.put(line)
    done_event.set()


def _drain_queue(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items


def _build_cmd(prompt, json_schema, add_dirs):
    cmd = [
        OPENCODE_BIN, "run",
        "--format", "json",
        # Equivalent of Claude Code's --dangerously-skip-permissions: the
        # pipeline is read-only analysis (reading source files + git checkout),
        # so auto-approve reads so the agent can freely inspect source files.
        "--auto",
    ]
    # OPENCODE_MODEL is the selector passed to `-m`. It must be a
    # "provider/model" selector that exists in the opencode config (e.g.
    # "volcengine-plan/ark-code-latest"). The model's `name` field in the
    # config holds the actual API model id.
    if OPENCODE_MODEL:
        cmd.extend(["-m", OPENCODE_MODEL])
    # opencode runs in a single --dir; absolute paths in the prompt still work.
    if add_dirs:
        cmd.extend(["--dir", add_dirs[0]])
    else:
        cmd.extend(["--dir", os.getcwd()])

    if json_schema:
        prompt += (
            "\n\nIMPORTANT: Output your answer as a single JSON code fence:\n"
            "```json\n<your JSON object matching the required schema>\n```\n"
            "Output ONLY that JSON code fence and nothing else."
        )

    cmd.append(prompt)
    return cmd


def _read_events(stream, event_queue, done_event):
    """Read `opencode run --format json` NDJSON from `stream` into `event_queue`.

    Each queued item is a (kind, payload) tuple where kind is "text" or "error".
    """
    for raw in iter(stream.readline, ""):
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "text":
            event_queue.put(("text", event.get("part", {}).get("text", "")))
        elif etype == "error":
            event_queue.put(("error", json.dumps(event.get("error", {}), ensure_ascii=False)))
    done_event.set()


def _extract_fenced_json(text):
    """Extract a ```json ... ``` block; fall back to the first balanced object."""
    start = text.find(_FENCE_OPEN)
    if start != -1:
        end = text.find(_FENCE_CLOSE, start + len(_FENCE_OPEN))
        if end != -1:
            candidate = text[start + len(_FENCE_OPEN):end].strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    # fall back: try to parse the whole text, then the longest balanced {...}
    for candidate in (text.strip(),):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return None


def call_opencode(
    prompt,
    json_schema=None,
    add_dirs=None,
    timeout=OPENCODE_TIMEOUT,
):
    prompt_bytes = len(prompt.encode("utf-8"))
    print(f"  [opencode] prompt size: {prompt_bytes:,} bytes", file=sys.stderr)
    if add_dirs:
        for d in add_dirs:
            print(f"  [opencode] dir: {d}", file=sys.stderr)

    cmd = _build_cmd(prompt, json_schema, add_dirs)

    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        print(f"  [opencode] ERROR: Binary not found at {OPENCODE_BIN}", file=sys.stderr)
        print(f"  [opencode] Install with: npm install @opencode-ai/cli -g  (or see https://opencode.ai)", file=sys.stderr)
        return None

    stderr_queue = queue.Queue()
    stderr_done = threading.Event()
    stderr_thread = threading.Thread(
        target=_enqueue_output, args=(proc.stderr, stderr_queue, stderr_done), daemon=True
    )
    stderr_thread.start()

    stdout_queue = queue.Queue()
    stdout_done = threading.Event()
    stdout_thread = threading.Thread(
        target=_read_events, args=(proc.stdout, stdout_queue, stdout_done), daemon=True
    )
    stdout_thread.start()

    text_parts = []
    start_time = time.time()
    last_display = ""
    timed_out = False

    # Consume events with a timeout watchdog. Because reads happen on a
    # background thread, the main loop can bail out and terminate opencode if
    # it stalls past `timeout`.
    while not stdout_done.is_set() or not stdout_queue.empty():
        try:
            kind, payload = stdout_queue.get(timeout=0.5)
        except queue.Empty:
            if proc.poll() is not None and stdout_queue.empty():
                break
            if time.time() - start_time > timeout:
                timed_out = True
                break
            elapsed = int(time.time() - start_time)
            sys.stderr.write(f"\r[opencode] ⏳ 运行中... ({elapsed}s)")
            sys.stderr.flush()
            continue

        if kind == "text":
            text_parts.append(payload)
            display = payload.rstrip("\n\r")[:120]
            if len(payload) > 120:
                display += "..."
            if display != last_display:
                if last_display:
                    sys.stderr.write(f"\033[K\r{last_display}\n")
                sys.stderr.write(f"\r[opencode] {display}")
                sys.stderr.flush()
                last_display = display
        elif kind == "error":
            print(f"  [opencode] stream error: {payload}", file=sys.stderr)

    if timed_out:
        print(f"\n  [opencode] WARNING: timeout after {int(time.time() - start_time)}s, terminating", file=sys.stderr)
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    if last_display:
        sys.stderr.write(f"\033[K\r{last_display}\n")

    stderr_text = "".join(_drain_queue(stderr_queue))

    if proc.returncode != 0:
        print(f"  [opencode] ERROR: exit code {proc.returncode}", file=sys.stderr)
        stderr_tail = stderr_text.strip()[-1000:] if stderr_text else ""
        stdout_tail = "".join(text_parts).strip()[-500:]
        if stderr_tail:
            print(f"  [opencode] stderr: {stderr_tail}", file=sys.stderr)
        if stdout_tail:
            print(f"  [opencode] stdout: {stdout_tail}", file=sys.stderr)
        return None

    stdout_text = "".join(text_parts)

    if not json_schema:
        return stdout_text.strip()

    structured = _extract_fenced_json(stdout_text)
    if structured is not None:
        return structured

    print(f"  [opencode] WARNING: could not parse JSON output, returning raw text", file=sys.stderr)
    print(f"  [opencode] stdout preview: {stdout_text[:500]}", file=sys.stderr)
    return stdout_text