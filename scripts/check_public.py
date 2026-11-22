"""Check the public source tree; optionally inspect reachable Git history.

Findings report file paths and rule names, never matched secrets or addresses.
This is a focused publication check, not a guarantee that a repository is safe.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = {".gitignore", "README.md", "PRIVACY.md", "CONTRIBUTING.md", "package.json", "server.js", "seed.js"}
TOP_DIRS = {"public", "docs", "scripts", "tests", ".github"}
PRIVATE = re.compile(r"(?i)(?:^|/)(?:data|gmail|Takeout|incoming|exports|\.git|\.local|dist|__pycache__|\.venv)(?:/|$)|(?:^|/)(?:memory\.md|\.env(?:\..*)?)$|\.(?:mbox|eml|sqlite3?|duckdb|db|pem|key|p12|log|zip|bak|backup)(?:[.-].*)?$")
RULES = {
    "private-home-path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "private-volume-path": re.compile(r"/Volumes/[A-Za-z0-9][A-Za-z0-9_. -]*/"),
    "personal-mail-address": re.compile(r"\b[A-Z0-9._%+-]+@(?:gmail|hotmail|outlook|yahoo|icloud|protonmail)\.com\b", re.I),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "access-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,}|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b"),
    "copied-mail-header": re.compile(r"(?m)^(?:Delivered-To|X-Gmail-Labels|Return-Path):"),
}

def public_files(root=ROOT):
    files = []
    for entry in sorted(root.iterdir()):
        if entry.name in TOP_FILES and entry.is_file():
            files.append(entry)
        elif entry.name in TOP_DIRS and entry.is_dir():
            files.extend(p for p in sorted(entry.rglob("*")) if p.is_file() and "__pycache__" not in p.parts)
    return files

def inspect_bytes(path, data):
    findings = []
    if PRIVATE.search(path):
        findings.append({"path": path, "rule": "private-data-file"})
    if b"\0" not in data[:8192]:
        value = data.decode("utf-8", errors="replace")
        findings += [{"path": path, "rule": name} for name, pattern in RULES.items() if pattern.search(value)]
    return findings

def scan(root=ROOT, history=False):
    files = public_files(root)
    findings = []
    for file in files:
        rel = file.relative_to(root).as_posix()
        if file.is_symlink():
            findings.append({"path": rel, "rule": "symlink"})
        findings.extend(inspect_bytes(rel, file.read_bytes()))
    # Staging or tracking an ignored private file must never go unnoticed.
    repository = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    has_history = repository.returncode == 0 and Path(repository.stdout.strip()).resolve() == root.resolve()
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout if has_history else b""
    for value in tracked.split(b"\0"):
        path = value.decode()
        if path and PRIVATE.search(path):
            findings.append({"path": path, "rule": "tracked-private-data"})
    commits = []
    if history and has_history:
        result = subprocess.run(["git", "-C", str(root), "log", "--all", "--format=%h%x09%ae%x09%ce"], capture_output=True, text=True, check=True)
        for entry in result.stdout.splitlines():
            sha, author, committer = entry.split("\t")
            if RULES["personal-mail-address"].search(author + " " + committer):
                commits.append(sha)
        objects = subprocess.run(["git", "-C", str(root), "rev-list", "--objects", "--all"], capture_output=True, text=True, check=True)
        seen = set()
        for entry in objects.stdout.splitlines():
            sha, _, path = entry.partition(" ")
            if not path or sha in seen:
                continue
            seen.add(sha)
            kind = subprocess.run(["git", "-C", str(root), "cat-file", "-t", sha], capture_output=True, text=True, check=True).stdout.strip()
            if kind == "blob":
                blob = subprocess.run(["git", "-C", str(root), "cat-file", "blob", sha], capture_output=True, check=True).stdout
                findings += [{**f, "object": sha[:12]} for f in inspect_bytes(path, blob)]
    return {"files_checked": len(files), "findings": findings, "commits_with_personal_email": commits,
            "history_checked": history and has_history, "history_unavailable": history and not has_history}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    result = scan(history=args.history)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["findings"] or result["commits_with_personal_email"] or result["history_unavailable"]))

if __name__ == "__main__":
    main()
