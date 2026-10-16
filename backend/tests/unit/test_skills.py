"""Skills: playbooks for common analyses. Each must parse, and name only tools and datasets that exist."""
import json
import re

import pandas as pd
import pytest

from app.agents.tools import analytics_tools, extraction_tools, read_skill_tool
from app.analysis import stats
from app.analysis.workspace import Workspace
from app.data.registry import CURATED
from app.data.store import SNAPSHOT_DIR
from app.skills import parse, skill_list, skills
from tests.unit.test_analysis import FakeStore


def test_every_skill_has_a_name_a_description_and_steps():
    assert len(skills()) >= 6
    for name, skill in skills().items():
        assert name == skill.name and re.fullmatch(r"[a-z_]+", name)
        assert len(skill.description) > 30 and skill.steps.count("\n") >= 2
    assert all(f"- {name}:" in skill_list() for name in skills())


def test_skills_name_only_tools_and_datasets_that_exist():
    """Every name in backticks must be a tool, a chart kind, a curated dataset, or one of their measures or
    dimensions: a renamed tool or column must not leave a playbook pointing at nothing."""
    workspace = Workspace(FakeStore())
    known = {t.name for t in extraction_tools(workspace) + analytics_tools(workspace)} | set(CURATED)
    known |= {"line", "bar", "grouped_bar", "stacked_bar", "scatter", "pie", "box", "dot", "heatmap", "map"}
    for key in CURATED:
        snapshot = pd.read_csv(SNAPSHOT_DIR / f"{key}.csv.gz", dtype=str)
        known |= set(snapshot.columns) | set(snapshot["measure"])
    for skill in skills().values():
        unknown = set(re.findall(r"`([a-z_0-9]+)`", skill.steps)) - known
        assert not unknown, f"{skill.name} names {sorted(unknown)}"


def test_read_skill_returns_the_steps_and_lists_the_skills():
    tool = read_skill_tool()
    assert "- haze:" in tool.description
    out = json.loads(tool.invoke({"name": "Haze", "reason": "x"}))
    assert out["skill"] == "haze" and "fire_hotspots" in out["steps"]
    with pytest.raises(stats.AnalysisError, match="unknown skill 'weather'"):
        tool.invoke({"name": "weather", "reason": "x"})


def test_a_skill_file_is_a_header_then_steps():
    skill = parse("---\nname: demo\ndescription: A demo: with a colon\n---\n1. Do this.\n2. Then that.\n")
    assert (skill.name, skill.description) == ("demo", "A demo: with a colon")
    assert skill.steps == "1. Do this.\n2. Then that."
