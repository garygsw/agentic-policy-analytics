"""The Extraction and Analytics agents: tool-using ReAct agents behind the Workers interface the graph calls."""
import json

from app.agents.tool_loop import run_tool_loop
from app.agents.tools import analytics_tools, extraction_tools
from app.analysis.workspace import Workspace, curated_catalogue
from app.data.store import DataStore
from app.graph import prompts


class AgentWorkers:
    def __init__(self, llm, store: DataStore | None = None):
        self.llm = llm
        self.store = store or DataStore()

    def extract(self, instructions: str, question: str) -> dict:
        ws = Workspace(self.store)
        summary = run_tool_loop(self.llm, node="extraction", tools=extraction_tools(ws), max_steps=6,
                                system=prompts.EXTRACTION.format(catalogue=curated_catalogue()),
                                task=f"Question: {question}\n\nTask from the coordinator: {instructions}")
        return {"summary": summary, "datasets": ws.profiles(), "sources": ws.sources}

    def analyse(self, instructions: str, question: str, extraction: dict) -> dict:
        ws = Workspace(self.store, sources=extraction.get("sources"))
        profiles = extraction.get("datasets") or []
        if not profiles:
            return {"summary": "No datasets were loaded, so nothing was computed.", "facts": [], "charts": [],
                    "sources": ws.sources}
        summary = run_tool_loop(self.llm, node="analytics", tools=analytics_tools(ws), max_steps=12,
                                system=prompts.ANALYTICS.format(datasets=json.dumps(profiles, ensure_ascii=False)),
                                task=f"Question: {question}\n\nTask from the coordinator: {instructions}")
        return {"summary": summary, "facts": ws.facts, "charts": ws.charts, "sources": ws.sources}
