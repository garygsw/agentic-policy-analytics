"""The Extraction and Analytics agents: tool-using ReAct agents behind the Workers interface the graph calls."""
import json

from app.agents.tool_loop import run_tool_loop
from app.agents.tools import analytics_tools, extraction_tools
from app.analysis.workspace import Workspace, curated_catalogue
from app.data.store import DataStore
from app.graph import prompts
from app.graph.options import current_options


def enabled(tools: list) -> list:
    """The tools this request allows (users can switch optional tools off)."""
    options = current_options()
    return [tool for tool in tools if options.tool_enabled(tool.name)]


class AgentWorkers:
    def __init__(self, llm, store: DataStore | None = None, retriever=None):
        self.llm = llm
        self.store = store or DataStore()
        self.retriever = retriever  # None: the shared document index, opened on first search

    def extract(self, instructions: str, question: str, previous: dict | None = None) -> dict:
        """`previous`: what earlier extraction steps of this turn loaded. Their datasets stay available and new
        sources continue the numbering, so S1 keeps meaning the same dataset."""
        previous = previous or {}
        ws = Workspace(self.store, sources=previous.get("sources"))
        task = f"Question: {question}\n\nTask from the coordinator: {instructions}"
        if previous.get("datasets"):
            task += "\n\nAlready loaded (no need to load again): " + "; ".join(
                f"{d['source_id']} {d['title']}" for d in previous["datasets"])
        summary = run_tool_loop(self.llm, node="extraction", tools=enabled(extraction_tools(ws, self.retriever)),
                                max_steps=6, system=prompts.EXTRACTION.format(catalogue=curated_catalogue()),
                                task=task)
        new = ws.profiles()
        kept = [d for d in previous.get("datasets", []) if d["dataset_id"] not in {n["dataset_id"] for n in new}]
        return {"summary": summary, "datasets": kept + new, "sources": ws.sources, "passages": len(ws.passages())}

    def analyse(self, instructions: str, question: str, extraction: dict, previous: dict | None = None) -> dict:
        """`previous`: an earlier analysis in this turn (e.g. before the reviewer asked for more). Its facts and
        charts are kept and new ones continue the numbering, so F1 keeps meaning the same number."""
        previous = previous or {}
        sources = list(extraction.get("sources") or [])
        sources += [s for s in previous.get("sources", []) if s["id"] not in {k["id"] for k in sources}]
        ws = Workspace(self.store, sources=sources, facts=previous.get("facts"), charts=previous.get("charts"))
        profiles = extraction.get("datasets") or []
        if not profiles:
            return {"summary": "No datasets were loaded, so nothing new was computed.", "facts": ws.facts,
                    "charts": ws.charts, "sources": ws.sources}
        task = f"Question: {question}\n\nTask from the coordinator: {instructions}"
        if ws.facts or ws.charts:
            task += (f"\n\nAlready computed and still valid (cite them; do not recompute): facts F1-F{len(ws.facts)}"
                     + "".join(f"; {c['id']} {c['title']}" for c in ws.charts))
        summary = run_tool_loop(self.llm, node="analytics", tools=enabled(analytics_tools(ws)), max_steps=12,
                                system=prompts.ANALYTICS.format(datasets=json.dumps(profiles, ensure_ascii=False)),
                                task=task)
        return {"summary": summary, "facts": ws.facts, "charts": ws.charts, "sources": ws.sources}
