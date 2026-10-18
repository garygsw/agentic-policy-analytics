#!/usr/bin/env python3
"""Set the API keys in .env without echoing them. Python stdlib only.

Required: ANTHROPIC_API_KEY and OPENAI_API_KEY (Claude does the main work; GPT checks it, runs short tasks and takes
over if Claude fails). Optional: TAVILY_API_KEY (web search) and LANGSMITH_API_KEY (tracing). Rarely needed, asked
only by name: FIRMS_MAP_KEY (rebuilding the fire-hotspot snapshot) and TYPESAFE_API_KEY (the triage trial).
For each key the script says why it is needed, how to get one and what it looks like.

Usage:
  python3 scripts/set_keys.py                     ask for the required and optional keys
  python3 scripts/set_keys.py --missing           ask only for keys that are not set yet
  python3 scripts/set_keys.py LANGSMITH_API_KEY   ask for one key
  python3 scripts/set_keys.py --verify            check the saved keys with each provider

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
# For each key: why it is needed, how to get one, and what it looks like. The examples are shortened shapes,
# not real keys. Lengths are typical, not guaranteed, so an unusual length asks for confirmation, not a refusal.
PROVIDERS = {
    "ANTHROPIC_API_KEY": {
        "label": "Anthropic (Claude)",
        "required": True,
        "purpose": "Claude does the main work: the coordinator, the agents and the report writer.",
        "steps": ["Sign in at https://platform.claude.com and add credit under Billing (pay as you go).",
                  "Open https://platform.claude.com/settings/keys and click 'Create Key'.",
                  "Copy the key straight away: it is shown only once."],
        "example": "sk-ant-api03-Xy7...(letters, digits, - and _)...Qw-AA",
        "typical": "about 108 characters",
        "prefixes": ("sk-ant-",),
        "length": (90, 130),
        "url": "https://api.anthropic.com/v1/models",
        "headers": lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    },
    "OPENAI_API_KEY": {
        "label": "OpenAI (GPT)",
        "required": True,
        "purpose": "GPT checks every report (the reviewer), runs short tasks, and takes over if Claude fails.",
        "steps": ["Sign in at https://platform.openai.com and add credit under Settings > Billing.",
                  "Open https://platform.openai.com/api-keys and click 'Create new secret key'.",
                  "Copy the key straight away: it is shown only once."],
        "example": "sk-proj-Ab3...(letters, digits, - and _)...x9Z",
        "typical": "about 130-170 characters (keys made before 2024: 51)",
        "prefixes": ("sk-",),
        "length": (40, 220),
        "url": "https://api.openai.com/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "TAVILY_API_KEY": {
        "label": "Tavily (web search)",
        "purpose": "Web search, for recent and announced policies. Without it web search is off, and agency "
                   "documents and datasets still work.",
        "steps": ["Sign up at https://app.tavily.com (a free plan is available).",
                  "Copy the API key shown on the dashboard."],
        "example": "tvly-dev-Ab3...x9Z",
        "typical": "about 40 characters",
        "prefixes": ("tvly-",),
        "length": (30, 64),
        "url": "https://api.tavily.com/usage",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
    },
    "LANGSMITH_API_KEY": {
        "label": "LangSmith (tracing)",
        "purpose": "Traces every graph step and LLM call in LangSmith (README, Observability). This sends prompts "
                   "and data to LangSmith. Saving a key also sets LANGSMITH_TRACING=true and a project name.",
        "steps": ["Sign up at https://smith.langchain.com.",
                  "Open Settings > API Keys and click 'Create API Key' (a personal access token).",
                  "Copy the key straight away: it is shown only once."],
        "example": "lsv2_pt_0a1b...(32 letters and digits)..._9f8e7d6c5b",
        "typical": "about 51 characters",
        "prefixes": ("lsv2_",),
        "length": (40, 64),
        "url": "https://api.smith.langchain.com/api/v1/workspaces",
        "headers": lambda key: {"x-api-key": key},
        "also_set": {"LANGSMITH_TRACING": "true", "LANGSMITH_PROJECT": "policy-analytics"},
    },
    "FIRMS_MAP_KEY": {
        "label": "NASA FIRMS (fire hotspots)",
        "purpose": "Only to rebuild the fire-hotspot snapshot (backend/scripts/make_hotspots.py). The committed "
                   "snapshot works without it.",
        "steps": ["Request a free map key at https://firms.modaps.eosdis.nasa.gov/api/map_key/ with your email.",
                  "Copy the key from the email."],
        "example": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
        "typical": "32 letters and digits",
        "prefixes": ("",),
        "length": (32, 32),
        "url": lambda key: f"https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={key}",
        "headers": lambda key: {},
        "rarely": True,
    },
    "TYPESAFE_API_KEY": {
        "label": "TypeSafe Jev (classifier trial)",
        "purpose": "Only for the triage classifier trial (backend/scripts/triage_trial.py). The app works without it.",
        "steps": ["Request an API key from TypeSafe AI (https://typesafe.ai)."],
        "example": "",
        "typical": "",
        "prefixes": ("",),
        "length": None,
        "url": "https://api.typesafe.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "rarely": True,
    },
}
REQUIRED = [name for name, p in PROVIDERS.items() if p.get("required")]


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


def problems(name: str, value: str) -> list[str]:
    """What looks wrong with a key's shape. A key can still be saved after confirmation."""
    provider, found = PROVIDERS[name], []
    if name == "OPENAI_API_KEY" and value.startswith("sk-ant-"):
        found.append("this looks like an Anthropic key")
    elif not value.startswith(provider["prefixes"]):
        found.append(f"expected it to start with {' or '.join(repr(p) for p in provider['prefixes'])}")
    low, high = provider["length"] or (1, 10_000)
    if not low <= len(value) <= high:
        found.append(f"it has {len(value)} characters; expected {provider['typical']}")
    return found


def looks_valid(name: str, value: str) -> bool:
    return not problems(name, value)


def guide(name: str, current: str, position: str = "") -> str:
    """What to show before asking for a key: why it is needed, how to get it, and what it looks like."""
    p = PROVIDERS[name]
    need = "REQUIRED" if p.get("required") else "optional, Enter to skip"
    lines = [f"{position}{name}: {p['label']} ({need})", f"  Why: {p['purpose']}", "  How to get one:"]
    lines += [f"    {i}. {step}" for i, step in enumerate(p["steps"], 1)]
    if p["example"]:
        lines.append(f"  Looks like: {p['example']}  ({p['typical']})")
    lines.append(f"  Now: {describe(current)}")
    return "\n".join(lines)


def is_git_ignored(path: Path) -> bool:
    """True if git ignores the file. Outside a git checkout there is nothing to protect against."""
    try:
        result = subprocess.run(["git", "check-ignore", "-q", path.name], cwd=path.parent, capture_output=True)
    except OSError:
        return True
    return result.returncode != 1  # 0 = ignored, 1 = not ignored, 128 = not a git checkout


def prompt_for(name: str, current: str, ask, confirm, position: str = "") -> str | None:
    """Show the key's guide and ask for a new value. None means keep the current one (or skip)."""
    print(guide(name, current, position))
    keep = "keep the current key" if current else ("skip" if not PROVIDERS[name].get("required") else "leave it unset")
    value = ask(f"  Paste the key (input hidden; Enter to {keep}): ").strip()
    print()
    if not value:
        return None
    if any(c.isspace() for c in value):
        print("  Not saved: the key contains spaces or line breaks.\n")
        return None
    found = problems(name, value)
    if found:
        answer = confirm(f"  This does not look like a {PROVIDERS[name]['label']} key: {'; '.join(found)}. "
                         "Save anyway? [y/N] ")
        if answer.strip().lower() != "y":
            print("  Not saved.\n")
            return None
    return value


def save_key(lines: list[str], name: str, value: str) -> list[str]:
    """Set the key, and whatever it switches on (a LangSmith key turns tracing on and names a project)."""
    lines = set_value(lines, name, value)
    for other, default in PROVIDERS[name].get("also_set", {}).items():
        if other == "LANGSMITH_TRACING" or not get_value(lines, other):
            lines = set_value(lines, other, default)
    return lines


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
    """List models (or show the key's status) with the key: proves it is accepted, costs no tokens."""
    provider = PROVIDERS[name]
    if not value:
        return (False, "not set") if provider.get("required") else (True, "not set (optional)")
    headers = {"User-Agent": "set-keys-check/1.0", **provider["headers"](value)}
    url = provider["url"](value) if callable(provider["url"]) else provider["url"]  # FIRMS takes the key in the URL
    try:
        request = urllib.request.Request(url, headers=headers)
        with opener(request, timeout=15, context=tls_context()) as response:
            json.load(response)
        return True, "OK"
    except urllib.error.HTTPError as e:
        hint = {401: " (key rejected)", 403: " (key rejected, or no permission)",
                429: " (rate limited or no credit)"}.get(e.code, "")
        return False, f"HTTP {e.code}{hint}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"request failed: {getattr(e, 'reason', e)}"


def main(argv: list[str] | None = None, ask=getpass.getpass, confirm=input) -> int:
    parser = argparse.ArgumentParser(description="Set the API keys in .env without echoing them.")
    parser.add_argument("names", nargs="*", metavar="NAME", help="keys to set (default: the required and optional keys; "
                        f"{', '.join(n for n, p in PROVIDERS.items() if p.get('rarely'))} only by name)")
    parser.add_argument("--verify", action="store_true", help="check the saved keys with each provider, then exit")
    parser.add_argument("--missing", action="store_true", help="only ask for keys that are not set yet")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    unknown = [n for n in args.names if n not in PROVIDERS]
    if unknown:
        parser.error(f"unknown key name(s): {', '.join(unknown)}")
    names = args.names or [n for n, p in PROVIDERS.items() if not p.get("rarely")]  # the rarely needed by name
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
    if args.missing:
        names = [n for n in names if not get_value(lines, n)]
    changed = []
    for i, name in enumerate(names, 1):
        value = prompt_for(name, get_value(lines, name), ask, confirm, f"[{i}/{len(names)}] ")
        if value is not None:
            lines = save_key(lines, name, value)
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
