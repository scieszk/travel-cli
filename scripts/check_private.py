#!/usr/bin/env python3
"""Reject common secrets and private runtime data without printing matched values."""

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys

RULES = {
    "provider-token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|dp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(r"(?i)(?:amap[_-]?key|api[_-]?key|access[_-]?token|client[_-]?secret|password)\b[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=._-]{20,})(?=[\"'\s]|$)"),
    "map-key-literal": re.compile(r"[\"'][a-fA-F0-9]{32}[\"']"),
    "platform-user-id": re.compile(r"\b(?:ou_|oc_)[a-f0-9]{24,}\b|@im\.wechat"),
    "private-network": re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    "embedded-url-credential": re.compile(r"https?://[^\s/:]+:[^\s/@]+@"),
}
PRIVATE_PARTS = {".secrets", ".git-credentials", "node_modules", ".venv", "__pycache__", "attachments", "logs", "outputs", "dist"}
PRIVATE_SUFFIXES = {".key", ".pem", ".db", ".sqlite", ".sqlite3", ".jsonl", ".log", ".zip", ".tgz", ".pptx", ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}


def sources(history=False, staged=False):
    if history:
        seen = set()
        for commit in subprocess.check_output(["git", "rev-list", "--all"]).decode().splitlines():
            tree = subprocess.check_output(["git", "ls-tree", "-rz", commit])
            for entry in filter(None, tree.split(b"\0")):
                metadata, filename = entry.split(b"\t", 1)
                _, kind, object_id = metadata.split()
                name = filename.decode("utf-8")
                if (name, object_id) in seen:
                    continue
                seen.add((name, object_id))
                if kind != b"blob":
                    raise SystemExit("Unsupported Git object requires manual review: " + name)
                yield name, subprocess.check_output(["git", "cat-file", "blob", object_id.decode()])
    else:
        command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged else ["git", "ls-files", "-z"]
        for name in filter(None, subprocess.check_output(command).decode("utf-8").split("\0")):
            yield name, subprocess.check_output(["git", "show", f":{name}"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--all", action="store_true")
    group.add_argument("--history", action="store_true", help="scan all reachable commit trees, including deleted files")
    args = parser.parse_args()
    findings = []
    count = 0
    for filename, raw in sources(history=args.history, staged=args.staged):
        count += 1
        path = PurePosixPath(filename)
        if set(path.parts) & PRIVATE_PARTS or path.suffix.lower() in PRIVATE_SUFFIXES or path.name == "waypoints.json" or (path.name.startswith(".env") and path.name != ".env.example"):
            findings.append((filename, 0, "private-file"))
            continue
        if b"\0" in raw:
            findings.append((filename, 0, "binary-file-needs-review"))
            continue
        content = raw.decode("utf-8", errors="replace")
        for rule, pattern in RULES.items():
            for match in pattern.finditer(content):
                findings.append((filename, content.count("\n", 0, match.start()) + 1, rule))
    for filename, line, rule in findings:
        print(f"BLOCKED {filename}:{line} [{rule}]", file=sys.stderr)
    if findings:
        print("Private-content check failed. Matched values are intentionally hidden.", file=sys.stderr)
        return 1
    print(f"Private-content check passed: {count} file versions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
