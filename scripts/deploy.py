#!/usr/bin/env python3
"""Deploy the platform with one command: keys, build, start, and wait until it answers. Python stdlib only.

Usage:
  python3 scripts/deploy.py                  first time: set up the keys, then build and start everything
  python3 scripts/deploy.py --keys           review every key again before starting
  python3 scripts/deploy.py --release v1.0.4 start the published images instead of building them

It is safe to run again: keys already set are not asked for, and Docker Compose rebuilds only what changed.
On Windows, run it with `python` instead of `python3`.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import set_keys  # noqa: E402  (the same guided key entry, checks and verification)

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
API, UI = "http://localhost:8000", "http://localhost:3000"


def step(text: str) -> None:
    print(f"\n== {text}")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, **kwargs)


def check_docker() -> str | None:
    """Docker with Compose v2, and its engine running. Returns what is missing, or None."""
    if not shutil.which("docker"):
        return "Docker is not installed. Install Docker Desktop (https://docs.docker.com/get-docker/), then run this again."
    if run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        return "Docker Compose v2 is missing. Update Docker Desktop, or install the compose plugin."
    if run(["docker", "info"], capture_output=True).returncode != 0:
        return f"Docker is installed but not running. Start it ({start_command()}), then run this again."
    return None


def start_command() -> str:
    """How to start Docker here: Docker Desktop, or Colima when the current Docker context is one of its VMs."""
    context = run(["docker", "context", "show"], capture_output=True, text=True).stdout.strip()
    if context.startswith("colima"):
        profile = context.removeprefix("colima").lstrip("-")
        return f"run: colima start {profile}".rstrip()
    return "open Docker Desktop, or start the Docker engine"


def set_up_keys(review: bool, ask=getpass.getpass, confirm=input) -> bool:
    """Ask for the required keys that are missing (and, the first time or with --keys, offer the optional ones).
    Returns False when a required key is still missing."""
    first_time = not ENV.exists()
    lines = set_keys.read_lines(ENV) or set_keys.read_lines(ROOT / ".env.example")
    if not set_keys.is_git_ignored(ENV):
        print("Refusing to write .env: git does not ignore it. Add it to .gitignore first.")
        return False
    missing = [n for n in set_keys.REQUIRED if not set_keys.get_value(lines, n)]
    names = [n for n, p in set_keys.PROVIDERS.items() if not p.get("rarely")] if review else missing
    if first_time and not review:
        print("Two keys are required: Anthropic and OpenAI. Two more are optional: Tavily (web search) and "
              "LangSmith (tracing).")
        if confirm("Set up the optional keys too? [y/N] ").strip().lower() == "y":
            names = missing + [n for n, p in set_keys.PROVIDERS.items()
                               if not p.get("required") and not p.get("rarely")]
    for i, name in enumerate(names, 1):
        value = set_keys.prompt_for(name, set_keys.get_value(lines, name), ask, confirm, f"[{i}/{len(names)}] ")
        if value is not None:
            lines = set_keys.save_key(lines, name, value)
            set_keys.write_lines(ENV, lines)  # saved as it goes, so an interrupted setup keeps what was entered
    still_missing = [n for n in set_keys.REQUIRED if not set_keys.get_value(lines, n)]
    if still_missing:
        print(f"Still missing: {', '.join(still_missing)}. Run this again when you have them.")
        return False
    if not ENV.exists():  # every key was already set in the environment: still give Compose its .env
        set_keys.write_lines(ENV, lines)
    return True


def verify_keys() -> bool:
    """Check every key that is set with its provider (free calls). A rejected required key stops the deploy."""
    lines, ok = set_keys.read_lines(ENV), True
    for name, provider in set_keys.PROVIDERS.items():
        value = set_keys.get_value(lines, name)
        if not value and not provider.get("required"):
            continue
        accepted, message = set_keys.verify(name, value)
        print(f"  {name}: {message}")
        if not accepted and provider.get("required"):
            ok = False
    if not ok:
        print("A required key was not accepted. Fix it with: python3 scripts/set_keys.py NAME")
    return ok


def get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def wait_until_up(timeout_s: float = 300) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if get_json(f"{API}/api/health") is not None:
            try:
                urllib.request.urlopen(UI, timeout=5).close()
                return True
            except (urllib.error.URLError, OSError):
                pass
        time.sleep(3)
    return False


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # our lines and Docker's stay in order, even in a log file
    parser = argparse.ArgumentParser(description="Deploy the platform with one command.")
    parser.add_argument("--keys", action="store_true", help="review every key before starting")
    parser.add_argument("--release", metavar="VERSION", help="start published images (e.g. v1.0.4) instead of building")
    parser.add_argument("--no-index", action="store_true", help="do not start building the document index")
    args = parser.parse_args(argv)

    step("Checking Docker")
    problem = check_docker()
    if problem:
        print(problem)
        return 1
    print("  Docker and Compose are ready.")

    step("API keys (.env)")
    if not set_up_keys(args.keys):
        return 1
    print("  Checking the keys with each provider (free calls):")
    if not verify_keys():
        return 1

    if args.release:
        step(f"Starting the published images {args.release}")
        files = ["-f", "docker-compose.yml", "-f", "docker-compose.release.yml"]
        environment = {**os.environ, "VERSION": args.release}
        code = run(["docker", "compose", *files, "pull"], env=environment).returncode
        if not code:
            code = run(["docker", "compose", *files, "up", "-d"], env=environment).returncode
    else:
        step("Building and starting (the first build takes a few minutes)")
        code = run(["docker", "compose", "up", "-d", "--build"]).returncode
    if code:
        print("Docker Compose failed; its output is above.")
        return code

    step("Waiting for the API and the web UI")
    if not wait_until_up():
        print("They did not answer within 5 minutes. See: docker compose logs api web")
        return 1
    ready = get_json(f"{API}/api/health/ready") or {}
    if str(ready.get("document_index", "")).startswith("not built") and not args.no_index:
        step("Building the document index in the background (about 40 minutes; the app works meanwhile)")
        run(["docker", "compose", "run", "-d", "--rm", "ingest", "python", "-m", "app.rag.scheduler", "--now"],
            capture_output=True)
        print("  Datasets work now; document search fills in as the index is built.")

    print(f"\nReady. Open the web UI: {UI}   (API docs: {API}/docs)")
    print("Logs: docker compose logs -f api   Stop: docker compose down   Keys: python3 scripts/set_keys.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
