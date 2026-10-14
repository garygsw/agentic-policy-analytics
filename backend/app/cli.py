"""Terminal client: ask a question, watch the agents work, answer their clarifying questions.

    uv run python -m app.cli "Analyse employment trends in the technology sector from 2020-2024"
    uv run python -m app.cli                      # interactive: several questions in one conversation
    uv run python -m app.cli --thread 1a2b3c4d    # continue an earlier conversation
"""
import argparse
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.config import REPO_ROOT
from app.graph.build import build_graph
from app.graph.checkpoint import sqlite_checkpointer
from app.guardrails.pii import rejection_message, screen
from app.llm.ledger import Ledger

CHECKPOINTS = REPO_ROOT / "var" / "checkpoints.sqlite"


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


def run_turn(graph, config: dict, payload) -> dict | None:
    """Stream one turn, resuming after every pause. Returns the final state, or None if the user left while
    the agents were waiting for an answer (the checkpoint keeps the paused conversation)."""
    while True:
        if payload is not None:
            for event in graph.stream(payload, config, stream_mode="custom"):
                print(f"  [{event['node']}] {event['kind']}: {event['content']}")
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


def print_result(values: dict, ledger: Ledger) -> None:
    totals = ledger.totals()
    tokens_in = totals["input_uncached"] + totals["cache_read"] + totals["cache_write"]
    print("\n" + values.get("answer", ""))
    print(f"\n[{values.get('outcome')}] {totals['llm_calls']} LLM calls ({totals['failed_llm_calls']} failed), "
          f"{tokens_in} input / {totals['output']} output tokens, ${totals['cost_usd']:.4f}"
          f"{' + unpriced calls' if totals['unpriced_calls'] else ''} | {', '.join(totals['models'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="ask one question and exit (default: interactive)")
    parser.add_argument("--thread", help="conversation ID to continue")
    parser.add_argument("--plan-approval", action="store_true", help="pause for approval before fetching data")
    args = parser.parse_args()

    graph = build_graph(checkpointer=sqlite_checkpointer(CHECKPOINTS))
    thread = args.thread or uuid.uuid4().hex[:8]
    print(f"Conversation {thread} (continue it later with --thread {thread})")
    if graph.get_state({"configurable": {"thread_id": thread}}).interrupts:  # resuming a paused conversation
        ledger = Ledger()
        values = run_turn(graph, {"configurable": {"thread_id": thread}, "callbacks": [ledger]}, None)
        if values is None:
            return
        print_result(values, ledger)
    while True:
        try:
            text = screened(args.question) if args.question else ask_until_clean("\nYou> ")
        except EOFError:
            return
        if not text or text.lower() in ("exit", "quit"):
            return
        ledger = Ledger()
        config = {"configurable": {"thread_id": thread}, "callbacks": [ledger]}
        values = run_turn(graph, config, {"messages": [HumanMessage(text)],
                                          "require_plan_approval": args.plan_approval})
        if values is None:
            return
        print_result(values, ledger)
        if args.question:
            return


if __name__ == "__main__":
    main()
