"""The ReAct loop each worker agent runs: the model reasons and picks a tool (action), the tool runs, and its
result (observation) goes back to the model, until the model answers without calling a tool."""
import json

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.graph.events import emit

OBSERVATION_PREVIEW = 300


def _thinking(content) -> str:
    """The model's own thinking, if the provider returned any (Claude thinking blocks, OpenAI reasoning
    summaries). Often empty: providers decide when to expose it."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(block.get("thinking", ""))
        elif isinstance(block, dict) and block.get("type") == "reasoning":
            parts += [s.get("text", "") for s in block.get("summary", [])]
    return "\n".join(p for p in parts if p.strip())


def run_tool_loop(llm, *, node: str, system: str, task: str, tools: list, max_steps: int = 8) -> str:
    model = llm("quality", tools=tools)
    by_name = {tool.name: tool for tool in tools}
    messages = [SystemMessage(system), HumanMessage(task)]
    for _ in range(max_steps):
        reply = model.invoke(messages)
        messages.append(reply)
        text = reply.content if isinstance(reply.content, str) else " ".join(
            b.get("text", "") for b in reply.content if isinstance(b, dict) and b.get("type") == "text")
        if thinking := _thinking(reply.content):
            emit(node, "thinking", thinking)
        if not reply.tool_calls:
            return text.strip()
        if text.strip():
            emit(node, "reasoning", text.strip())
        for call in reply.tool_calls:
            args = {k: v for k, v in call["args"].items() if k != "reason"}
            emit(node, "status", call["args"].get("reason") or f"Running {call['name']}")
            emit(node, "action", f"{call['name']}({json.dumps(args, ensure_ascii=False)})",
                 reason=call["args"].get("reason", ""))
            try:
                result = by_name[call["name"]].invoke(call["args"])
            except Exception as error:  # noqa: BLE001 - errors are observations the model can correct
                result = f"ERROR: {error}"
            emit(node, "observation", str(result)[:OBSERVATION_PREVIEW], detail=str(result))
            messages.append(ToolMessage(str(result), tool_call_id=call["id"], name=call["name"]))
    emit(node, "error", f"Stopped after {max_steps} steps")
    return f"Stopped after {max_steps} tool steps; results so far are recorded."
