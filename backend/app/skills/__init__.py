"""Skills: short playbooks for common analyses, one Markdown file each in this folder.

The coordinator's prompt lists each skill's name and description, so it can name one in its instructions. The
extraction and analytics agents read a skill's steps with the read_skill tool when one fits: the full text enters
an agent's context only when it is needed, and the trace shows which skill was used.
"""
from dataclasses import dataclass
from functools import cache
from pathlib import Path

SKILLS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    steps: str


def parse(text: str) -> Skill:
    """A skill file: a header between "---" lines (name: ..., description: ...), then the steps."""
    _, header, steps = text.split("---", 2)
    fields = dict(line.split(":", 1) for line in header.strip().splitlines())
    return Skill(fields["name"].strip(), fields["description"].strip(), steps.strip())


@cache
def skills() -> dict[str, Skill]:
    return {skill.name: skill for skill in (parse(p.read_text()) for p in sorted(SKILLS_DIR.glob("*.md")))}


def skill_list() -> str:
    """One line per skill, for prompts and the tool's description."""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills().values())
