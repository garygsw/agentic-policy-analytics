"""Terminal client: ask a question, watch the agents work, answer their clarifying questions.

    uv run python -m app.cli "Analyse employment trends in the technology sector from 2020-2024"
    uv run python -m app.cli                      # interactive: several questions in one conversation
    uv run python -m app.cli --thread 1a2b3c4d    # continue an earlier conversation
    uv run python -m app.cli --options            # list the models and tools that can be chosen

Each status line shows the elapsed time, the tokens and cost so far, and what is happening now.
"""
import argparse
import json
import time
import uuid

from langchain_core.messages import HumanMessage
from langchain_core.utils.json import parse_partial_json
from langgraph.types import Command

from app.config import REPO_ROOT
from app.graph.build import build_graph
from app.graph.checkpoint import sqlite_checkpointer
from app.graph.options import RunOptions, available_models, available_tools
from app.guardrails.pii import rejection_message, screen
from app.llm.ledger import Ledger

CHECKPOINTS = REPO_ROOT / "var" / "checkpoints.sqlite"
REPORTS = REPO_ROOT / "var" / "reports"


def screened(text: str) -> str | None:
    """The text, or None after explaining why it was rejected. Runs before anything reaches the graph."""
    findings = screen(text)
    if findings:
        print(rejection_message(findings))
        return None
    return text


def ask_until_clean(prompt: str) -> str:
    while True:
        answer = screened(input(prompt).strip())
        if answer is not None:
            return answer


def plan_decision(text: str) -> dict:
    if text.lower() in ("y", "yes"):
        return {"approved": True}
    return {"approved": False, "feedback": "" if text.lower() in ("", "n", "no") else text}


VERBOSITY = {"brief": 1, "standard": 2, "full": 3}


class Display:
    """Prints events up to the chosen level, each status line prefixed with elapsed time, tokens and cost."""

    def __init__(self, verbosity: str):
        self.level = VERBOSITY[verbosity]
        self.started = time.time()
        self.tokens, self.cost = 0, 0.0
        self.draft_id, self.draft_text, self.draft_shown = None, "", 0

    def draft(self, chunk, metadata: dict) -> None:
        """Stream the report summary as the writer produces it (option A: shown before the check, labelled)."""
        if metadata.get("langgraph_node") != "report_writer":
            return
        if chunk.id != self.draft_id:  # a new draft (first one, or a revision)
            self.draft_id, self.draft_text, self.draft_shown = chunk.id, "", 0
            print("\n  Draft (checking numbers…): ", end="", flush=True)
        content = chunk.content
        self.draft_text += content if isinstance(content, str) else "".join(
            b.get("text", "") for b in content if isinstance(b, dict))
        summary = (parse_partial_json(self.draft_text) or {}).get("summary", "") if self.draft_text else ""
        if isinstance(summary, str) and len(summary) > self.draft_shown:
            print(summary[self.draft_shown:], end="", flush=True)
            self.draft_shown = len(summary)

    def show(self, event: dict) -> None:
        if event["node"] == "validate" and "passed" in event:
            print("\n  " + ("✓ Checked: every number matches a computed fact" if event["passed"]
                             else f"Revised after check: {len(event['issues'])} problem(s) sent back to the writer"))
        if event["kind"] == "usage":
            self.tokens = event["input_total"] + event["output"]
            self.cost = event["cost_usd"]
            return
        if event["level"] > self.level:
            return
        if event["kind"] == "status":
            elapsed = int(event["ts"] - self.started)
            print(f"[{elapsed // 60}:{elapsed % 60:02d} · {self.tokens / 1000:.1f}k tokens · ${self.cost:.3f}] "
                  f"{event['node']}: {event['content']}")
        elif event["kind"] == "context":
            parts = " · ".join(f"{k} {v:,}" for k, v in (event.get("breakdown") or {}).items() if v)
            print(f"      context {event['content']} [{event.get('model')}, effort {event.get('effort')}]: {parts}")
        else:
            print(f"      {event['kind']}: {event['content']}")


def run_turn(graph, config: dict, payload, display: Display) -> dict | None:
    """Stream one turn, resuming after every pause. Returns the final state, or None if the user left while
    the agents were waiting for an answer (the checkpoint keeps the paused conversation)."""
    while True:
        if payload is not None:
            for mode, chunk in graph.stream(payload, config, stream_mode=["custom", "messages"]):
                if mode == "custom":
                    display.show(chunk)
                else:
                    display.draft(*chunk)
        snapshot = graph.get_state(config)
        if not snapshot.interrupts:
            return snapshot.values
        request = snapshot.interrupts[0].value
        try:
            if request["kind"] == "clarification":
                payload = Command(resume=ask_until_clean(f"\n? {request['question']}\n> "))
            else:
                reply = ask_until_clean(f"\nProposed plan: {request['plan']}\nApprove? [y / n / or feedback] > ")
                payload = Command(resume=plan_decision(reply))
        except EOFError:
            thread = config["configurable"]["thread_id"]
            print(f"\n(No answer given. The conversation is paused; continue it with --thread {thread})")
            return None


def print_calls(ledger: Ledger) -> None:
    """The LLM call log: one line per call, with an estimate of what filled the context window."""
    header = ("agent", "model", "ok", "secs", "in", "cached", "out", "cost $")
    print("\n{:16s} {:28s} {:3s} {:>5s} {:>7s} {:>7s} {:>6s} {:>8s}  tools".format(*header))
    for c in ledger.calls:
        model = f"{c.provider}:{c.model}"
        cost = f"{c.cost_usd:.4f}" if c.cost_usd is not None else "-"
        tools = ",".join(c.tool_calls)
        print(f"{(c.agent or '-')[:16]:16s} {model[:28]:28s} {'yes' if c.ok else 'NO':3s} {c.latency_ms / 1000:5.1f} "
              f"{c.usage.input_total:7d} {c.usage.cache_read:7d} {c.usage.output:6d} {cost:>8s}  {tools}")
    if ledger.tools:
        slow = sorted(ledger.tools, key=lambda t: -t.latency_ms)[:3]
        print("slowest tools: " + ", ".join(f"{t.name} {t.latency_ms / 1000:.1f}s" for t in slow))
    last = next((c for c in reversed(ledger.calls) if c.ok and c.agent == "analytics"), None)
    if last:
        print("context of the last analytics call (estimated tokens): "
              + ", ".join(f"{k} {v}" for k, v in last.context_estimate.items() if v))


def save_report(values: dict, thread: str) -> str | None:
    if not values.get("report_html"):
        return None
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{thread}-{time.strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(values["report_html"], encoding="utf-8")
    return str(path)


def print_result(values: dict, ledger: Ledger, display: Display, thread: str) -> None:
    totals = ledger.totals()
    elapsed = int(time.time() - display.started)
    print("\n" + values.get("answer", ""))
    if report_path := save_report(values, thread):
        print(f"\nHTML report: {report_path}")
    print(f"\n[{values.get('outcome')}] {elapsed // 60}:{elapsed % 60:02d} · {totals['llm_calls']} LLM calls "
          f"({totals['failed_llm_calls']} failed) · {totals['input_total']:,} input / {totals['output']:,} output "
          f"tokens ({totals['cache_read']:,} cached) · ${totals['cost_usd']:.4f}"
          f"{' + unpriced calls' if totals['unpriced_calls'] else ''} · {', '.join(totals['models'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="ask one question and exit (default: interactive)")
    parser.add_argument("--thread", help="conversation ID to continue")
    parser.add_argument("--plan-approval", action="store_true", help="pause for approval before fetching data")
    parser.add_argument("--calls", action="store_true", help="print the LLM call log after each answer")
    parser.add_argument("--verbosity", choices=list(VERBOSITY), default="standard",
                        help="brief: status and actions; standard: plus reasoning; full: plus thinking and context")
    parser.add_argument("--model", help="main model (see --options)")
    parser.add_argument("--effort", choices=["low", "medium", "high"], help="reasoning effort (default: low)")
    parser.add_argument("--tools", help="comma-separated optional tools to enable (default: all; see --options)")
    parser.add_argument("--triage", choices=["local", "jev", "llm"], default="local",
                        help="who makes the fast triage decision (default: local, unsure cases to the LLM)")
    parser.add_argument("--options", action="store_true", help="list the models and tools that can be chosen")
    args = parser.parse_args()
    if args.options:
        print(json.dumps({"models": available_models(), "tools": available_tools()}, indent=1))
        return
    options = RunOptions(model=args.model, reasoning_effort=args.effort, triage=args.triage,
                         tools=None if args.tools is None else [t for t in args.tools.split(",") if t])

    graph = build_graph(checkpointer=sqlite_checkpointer(CHECKPOINTS))
    thread = args.thread or uuid.uuid4().hex[:8]
    print(f"Conversation {thread} (continue it later with --thread {thread})")
    def config(ledger: Ledger) -> dict:
        return {"configurable": {"thread_id": thread, "options": options.model_dump()}, "callbacks": [ledger]}

    if graph.get_state({"configurable": {"thread_id": thread}}).interrupts:  # resuming a paused conversation
        ledger, display = Ledger(), Display(args.verbosity)
        values = run_turn(graph, config(ledger), None, display)
        if values is None:
            return
        print_result(values, ledger, display, thread)
    while True:
        try:
            text = screened(args.question) if args.question else ask_until_clean("\nYou> ")
        except EOFError:
            return
        if not text or text.lower() in ("exit", "quit"):
            return
        ledger, display = Ledger(), Display(args.verbosity)
        values = run_turn(graph, config(ledger), {"messages": [HumanMessage(text)],
                                                  "require_plan_approval": args.plan_approval}, display)
        if values is None:
            return
        print_result(values, ledger, display, thread)
        if args.calls:
            print_calls(ledger)
        if args.question:
            return


if __name__ == "__main__":
    main()
