"""Short titles for conversations, for the history list: "Tech employment since 2019" rather than the whole first
question. The fast model writes one in the background; until it arrives, or if it fails, the question cut at a
word boundary is used."""
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.providers import chat

MAX_CHARS = 50
PROMPT = ("Write a short title, 3 to 6 words, for a conversation that starts with the user's question. Name the topic "
          "(e.g. 'COE premiums since 2020'). Use the question's language. No quotation marks, no full stop.")


class Title(BaseModel):
    title: str


def short_question(question: str) -> str:
    """The question cut at a word boundary: the title until (or unless) a written one arrives."""
    text = " ".join(question.split())
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def write_title(question: str, llm=chat) -> str:
    try:
        reply = llm("fast", schema=Title).invoke([SystemMessage(PROMPT), HumanMessage(question)])
        title = reply.title.strip().strip("\"'“”").rstrip(".。")
    except Exception:  # noqa: BLE001 - a title is never worth failing over
        return short_question(question)
    return title[:60] if title else short_question(question)
