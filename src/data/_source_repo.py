#!/usr/bin/env python3
import os
import subprocess
import shutil

REPO_CLONE_DIR = "repos"

KNOWN_REPOS = {
    "vllm-project/vllm": {
        "dir_name": "vllm",
        "url": "https://github.com/vllm-project/vllm.git",
        "common_paths": [
            os.path.expanduser("~/code/vllm"),
            os.path.expanduser("~/projects/vllm"),
            os.path.expanduser("~/vllm"),
            os.path.join(os.getcwd(), "repos", "vllm"),
        ],
    },
    "vllm-project/vllm-ascend": {
        "dir_name": "vllm-ascend",
        "url": "https://github.com/vllm-project/vllm-ascend.git",
        "common_paths": [
            os.path.expanduser("~/code/vllm-ascend"),
            os.path.expanduser("~/projects/vllm-ascend"),
            os.path.expanduser("~/vllm-ascend"),
            os.path.join(os.getcwd(), "repos", "vllm-ascend"),
        ],
    },
}


def repo_dir_name(repo):
    config = KNOWN_REPOS.get(repo)
    if config and "dir_name" in config:
        return config["dir_name"]
    return repo.split("/")[-1]


def ensure_repo(repo, local_path=None, project_dir=None, branch="main", skip_pull=False):
    local = resolve_local_repo(repo, local_path, project_dir)
    if local is None:
        return None

    if not skip_pull:
        pull_repo(local, repo, branch)
    return local


def resolve_local_repo(repo, local_path=None, project_dir=None):
    if local_path:
        if os.path.isdir(local_path):
            if _is_correct_repo(local_path, repo):
                print(f"Using user-specified local repo: {local_path}")
                return local_path
            print(f"Warning: {local_path} does not appear to be {repo}, falling back")
        else:
            print(f"Warning: User-specified path {local_path} does not exist, falling back")

    config = KNOWN_REPOS.get(repo)
    if config:
        for p in config["common_paths"]:
            if os.path.isdir(p) and _is_correct_repo(p, repo):
                print(f"Found local repo at common path: {p}")
                return p

    if project_dir:
        clone_dest = os.path.join(project_dir, REPO_CLONE_DIR, repo_dir_name(repo))
        if os.path.isdir(clone_dest) and _is_correct_repo(clone_dest, repo):
            print(f"Using cloned repo: {clone_dest}")
            return clone_dest

        clone_url = config["url"] if config else f"https://github.com/{repo}.git"
        print(f"Cloning {repo} to {clone_dest}...")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, clone_dest],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            print(f"Cloned {repo} to {clone_dest}")
            return clone_dest
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"Failed to clone {repo}: {e}")
            return None

    print(f"No local source available for {repo}")
    return None


def pull_repo(local_path, repo, branch="main"):
    print(f"Pulling latest changes for {repo} at {local_path}...")

    upstream = _find_upstream_remote(local_path, repo)

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", upstream, branch],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                print(f"  Already up to date")
            else:
                print(f"  Updated: {output[:100]}")
        else:
            print(f"  Pull failed (non-fast-forward or error): {result.stderr[:100]}")
            print(f"  Continuing with current state")
    except subprocess.TimeoutExpired:
        print(f"  Pull timed out, continuing with current state")
    except Exception as e:
        print(f"  Pull error: {e}, continuing with current state")


def _find_upstream_remote(local_path, repo):
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return "origin"

        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 2:
                continue
            remote_name = parts[0]
            remote_url = parts[1]
            if _url_matches_repo(remote_url, repo):
                return remote_name

        return "origin"
    except Exception:
        return "origin"


def _url_matches_repo(url, repo):
    url_clean = url.replace(".git", "").rstrip("/")
    target = f"/{repo}"
    if url_clean.endswith(target) or url_clean == repo:
        return True
    ssh_url = f"git@github.com:{repo}"
    if url == ssh_url or url == ssh_url + ".git":
        return True
    return False


def _is_correct_repo(local_path, repo):
    git_dir = os.path.join(local_path, ".git")
    if not os.path.isdir(git_dir):
        return False

    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 2:
                continue
            remote_url = parts[1].replace(".git", "").rstrip("/")
            target = f"/{repo}"
            if remote_url.endswith(target) or remote_url == repo:
                return True
            ssh_url = f"git@github.com:{repo}"
            if parts[1] == ssh_url or parts[1] == ssh_url + ".git":
                return True

        return False
    except Exception:
        return False


def get_current_sha(local_path):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


