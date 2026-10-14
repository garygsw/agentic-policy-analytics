#!/usr/bin/env python3
"""Set the LLM API keys in .env without echoing them. Python stdlib only.

Usage:
  python3 scripts/set_keys.py                   prompt for every key
  python3 scripts/set_keys.py TAVILY_API_KEY    prompt for one key
  python3 scripts/set_keys.py --verify          check the saved keys with each provider

Input is hidden and keys are never printed (only their last 4 characters).
The file is written atomically with owner-only permissions, and the script
refuses to write it if git would not ignore it.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each check is a free metadata request (list models / show usage), never a billed call.
# The verification URLs are fixed here, so a *_BASE_URL variable in the shell cannot redirect a key.
PROVIDERS = {
    "OPENAI_API_KEY": {
        "label": "OpenAI (primary)",
        "prefix": "sk-",
        "url": "https://api.openai.com/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "ANTHROPIC_API_KEY": {
        "label": "Anthropic (fallback)",
        "prefix": "sk-ant-",
        "url": "https://api.anthropic.com/v1/models",
        "headers": lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    },
    "TAVILY_API_KEY": {
        "label": "Tavily (web search tool, optional)",
        "prefix": "tvly-",
        "url": "https://api.tavily.com/usage",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "optional": True,
    },
    "TYPESAFE_API_KEY": {
        "label": "TypeSafe Jev (classifier trial, optional)",
        "prefix": "",
        "url": "https://api.typesafe.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "optional": True,
    },
}


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def get_value(lines: list[str], name: str) -> str:
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip().strip("'\"")
    return ""


def set_value(lines: list[str], name: str, value: str) -> list[str]:
    """Replace the NAME= line in place, or append one. Every other line is kept as it is."""
    new_line = f"{name}={value}"
    for i, line in enumerate(lines):
        key, sep, _ = line.partition("=")
        if sep and key.strip() == name:
            return lines[:i] + [new_line] + lines[i + 1:]
    return lines + [new_line]


def write_lines(path: Path, lines: list[str]) -> None:
    """Atomic write; mkstemp already creates the file readable by the owner only."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def describe(value: str) -> str:
    return f"set (ends ...{value[-4:]})" if value else "not set"


def looks_valid(name: str, value: str) -> bool:
    if name == "OPENAI_API_KEY" and value.startswith("sk-ant-"):
        return False  # an Anthropic key pasted into the OpenAI slot
    return value.startswith(PROVIDERS[name]["prefix"])


def is_git_ignored(path: Path) -> bool:
    """True if git ignores the file. Outside a git checkout there is nothing to protect against."""
    try:
        result = subprocess.run(["git", "check-ignore", "-q", path.name], cwd=path.parent, capture_output=True)
    except OSError:
        return True
    return result.returncode != 1  # 0 = ignored, 1 = not ignored, 128 = not a git checkout


def prompt_for(name: str, current: str, ask, confirm) -> str | None:
    """Ask for a new value. None means keep the current one."""
    label = PROVIDERS[name]["label"]
    print(f"{name} - {label}: {describe(current)}")
    value = ask("  New key (input hidden, Enter to keep): ").strip()
    if not value:
        return None
    if any(c.isspace() for c in value):
        print("  Not saved: the key contains whitespace.")
        return None
    if not looks_valid(name, value):
        answer = confirm(f"  This does not look like a {label} key "
                         f"(expected '{PROVIDERS[name]['prefix']}...'). Save anyway? [y/N] ")
        if answer.strip().lower() != "y":
            print("  Not saved.")
            return None
    return value


def tls_context() -> ssl.SSLContext:
    """Default trust store, or the OS bundle when this Python build ships none (seen with some pyenv builds)."""
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.isfile(bundle):
                context.load_verify_locations(cafile=bundle)
                break
    return context


def verify(name: str, value: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """List models with the key: proves it is accepted, costs no tokens."""
    provider = PROVIDERS[name]
    if not value:
        return (True, "not set (optional)") if provider.get("optional") else (False, "not set")
    headers = {"User-Agent": "set-keys-check/1.0", **provider["headers"](value)}
    try:
        request = urllib.request.Request(provider["url"], headers=headers)
        with opener(request, timeout=15, context=tls_context()) as response:
            json.load(response)
        return True, "OK"
    except urllib.error.HTTPError as e:
        hint = {401: " (key rejected)", 403: " (no permission)", 429: " (rate limited or no credit)"}.get(e.code, "")
        return False, f"HTTP {e.code}{hint}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"request failed: {getattr(e, 'reason', e)}"


def main(argv: list[str] | None = None, ask=getpass.getpass, confirm=input) -> int:
    parser = argparse.ArgumentParser(description="Set the LLM API keys in .env without echoing them.")
    parser.add_argument("names", nargs="*", metavar="NAME", help=f"keys to set (default: all of {', '.join(PROVIDERS)})")
    parser.add_argument("--verify", action="store_true", help="check the saved keys with each provider, then exit")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    unknown = [n for n in args.names if n not in PROVIDERS]
    if unknown:
        parser.error(f"unknown key name(s): {', '.join(unknown)}")
    names = args.names or list(PROVIDERS)
    env_file = args.env_file
    # A new .env starts from .env.example, so its comments and other settings carry over.
    lines = read_lines(env_file) or read_lines(env_file.with_name(".env.example"))

    if args.verify:
        results = {name: verify(name, get_value(lines, name)) for name in names}
        for name, (_, message) in results.items():
            print(f"{name}: {message}")
        return 0 if all(ok for ok, _ in results.values()) else 1

    if not is_git_ignored(env_file):
        print(f"Refusing to write {env_file.name}: git does not ignore it. Add it to .gitignore first.")
        return 1
    changed = []
    for name in names:
        value = prompt_for(name, get_value(lines, name), ask, confirm)
        if value is not None:
            lines = set_value(lines, name, value)
            changed.append(name)
    if not changed:
        print("No changes.")
        return 0
    write_lines(env_file, lines)
    print(f"Saved {', '.join(changed)} to {env_file.name} (owner-only permissions).")
    print("Check them with: python3 scripts/set_keys.py --verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
