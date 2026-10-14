# Decision log

Each entry records the date, the decision, the alternatives considered and the reasoning. Entries marked **Open** are waiting on a decision.

## D-001 · 2026-10-14 · Secrets and local files stay out of the repo
- **Decision:** API keys live only in `.env` (gitignored). They are set with `scripts/set_keys.py`, which hides input, never prints a key, writes the file with owner-only permissions, and refuses to write if git would not ignore the file. Assistant instruction files and local tool configuration are gitignored too, and a local pre-commit check blocks them and anything that looks like an API key.
- **Alternatives:** Exporting keys in the shell profile. A secrets manager, which is the production path (see ARCHITECTURE.md) but overkill for local development.
- **Why:** The repository is public.

## D-002 · 2026-10-14 · LLM providers: OpenAI primary, Anthropic fallback
- **Decision:** OpenAI is the primary provider and Anthropic the automatic fallback, behind one interface. A `FakeLLM` serves tests, and a flag forces a primary failure for demos.
- **Alternatives:** Adding Gemini as a third provider. Claude through AWS Bedrock.
- **Why:** These are the keys available. The brief lists AWS Bedrock, OpenAI, GCP Gemini and Azure OpenAI. The direct Anthropic API is a deliberate deviation: it serves the same model family as Claude on Bedrock, and the provider interface keeps a Bedrock adapter a small addition.

## D-003 · 2026-10-14 · ReAct step format: JSON validated by pydantic (superseded by D-008)
- **Decision:** Every agent step is a JSON object `{thought, action, args}`, validated against a pydantic model. Malformed output gets one retry that includes the validation error.
- **Alternatives:** Each provider's native tool calling.
- **Why:** One format works for both providers and the FakeLLM, and the reasoning is explicit and can be streamed to the UI as it happens.

## D-004 · 2026-10-14 · The Coordinator is itself a ReAct agent
- **Decision:** The Coordinator chooses among `delegate_extraction`, `delegate_analytics`, `ask_clarification`, `replan` and `finish`, under a step limit.
- **Alternatives:** A fixed plan run by a deterministic executor, with the LLM called again only to replan after a failure.
- **Why:** The decisions (what to fetch, when to ask, when to replan) are made by the agent and visible in its trace.

## D-005 · 2026-10-14 · Structured data: snapshots, a mock internal API, and one live API with cached fallback
- **Decision:** Committed CSV and Excel snapshots of official statistics, a mock JSON API for "internal" data, and one live public API that falls back to its snapshot when it fails.
- **Alternatives:** Snapshots only, which leaves no real API failure to demonstrate.
- **Note:** Each dataset's licence is checked and recorded in DATA_SOURCES.md before its snapshot is committed.

## D-006 · 2026-10-14 · Unstructured context through RAG
- **Decision:** Policy documents, press releases, speeches, public statements and parliamentary material are ingested by a separate ingest service on a cron schedule. Each source type has its own chunking strategy. Every chunk carries citation metadata (title, publisher, date, URL, section or page) into the report.
  - **Store:** SQLite. FTS5 provides BM25 keyword search, a vector index sits in the same file, and the two are merged by reciprocal rank fusion.
  - **Embeddings:** a free local model, used at both ingestion and query time. The index records the model name, and a query refuses to run if the model differs.
  - **Reranking:** a local cross-encoder.
  - **Search access:** hybrid, keyword-only and vector-only modes, both as API endpoints and as an agent tool.
- **Alternatives:** LanceDB or a Qdrant service for storage. Hosted embeddings, which would have no fallback because only one of the two providers offers embeddings. An LLM as reranker, which is slower and costs tokens.
- **Why:** A single local stack works offline and at no cost, and it keeps working when an LLM provider is down.
- **Copyright:** Fetched documents are cached locally and never committed, because some agencies' terms forbid republication without permission. The repo holds only source manifests and synthetic test fixtures.
- **Open:** Which sources come first.

## D-007 · 2026-10-14 · HTML reports rendered from a validated structure
- **Decision:** The LLM writes a structured report: sections, claims with citation IDs, and chart IDs. It is validated by pydantic and checked for grounding and citations, then a template renders it as self-contained HTML in a clean, minimal reading style. Charts come from a chart tool that takes column references, never numbers.
- **Alternatives:** Letting the LLM write HTML directly.
- **Why:** The checks run on structured data, and model-written markup never reaches the page.

## D-008 · 2026-10-14 · LangGraph with checkpoints and human-in-the-loop
- **Decision:**
  - The agents run as a LangGraph `StateGraph`, whose state holds the multi-turn conversation: messages, detected language, triage result, plan, data, citations and report.
  - A SQLite checkpointer stores state per conversation thread. API requests are therefore stateless: any request resumes a thread from its checkpoint.
  - The graph pauses with `interrupt()` for human input, for example to ask a clarifying question, and resumes when the user answers.
  - Agents use the providers' native tool calling through LangChain chat models, with provider fallback. This supersedes D-003.
- **Alternatives:** A custom ReAct loop. LangGraph for the top-level flow only.
- **Why:** Checkpointing, interrupts and streaming come built in. The framework is familiar to the maintainer, which makes the code easier to review.
- **Note:** SQLite limits this to one host. Running several API replicas would need a Postgres checkpointer.

## D-009 · 2026-10-14 · Reliability and observability
- **Decision:**
  - A reviewer node judges whether the analysis answers the question, and can send work back once.
  - A deterministic template report, built only from computed numbers, is the last resort when both providers fail or grounding fails twice.
  - Run events are stored with sequence numbers, so a client that connects late replays every event before following live.
  - Every LLM call is logged: provider, model, input, output and cache read/write tokens, latency, cost and the tools it called, plus an estimated breakdown of the context window.
- **Why:** Failures should degrade the answer visibly rather than crash the run, and cost and fallback behaviour should be measurable.

## D-010 · 2026-10-14 · Input guardrails, triage and languages
- **Decision:**
  - A local screen rejects personal identifiers before anything is sent to an LLM provider or stored in a checkpoint.
  - Language is detected locally and kept in graph state. Queries are translated to English before retrieval, and answers come back in the user's most recent language.
  - Off-topic queries are redirected to what the platform is for.
  - Vague queries get clarifying questions through an interrupt.
  - Unanswerable queries are declined honestly.
- **Open:** Which model makes the fast classification decisions, the exact rules for personal data, and which languages are supported.

## D-011 · 2026-10-14 · Evaluation with standard metrics
- **Decision:**
  - Groundedness: numbers are checked against computed values, and claims against the passages they cite.
  - Retrieval: recall@k and MRR on a labelled query set.
  - Subjective quality: an LLM-as-a-judge with a written rubric.
  - Consistency: the same question asked repeatedly.
  - Guardrail classifiers: precision and recall.
- **Open:** Whether to use our own implementations or an evaluation library.

## D-012 · 2026-10-14 · No hosted demo
- **Decision:** No public live site. Reviewers run the stack locally with Docker Compose, and the README will include a demo video once the app is complete.
- **Why:** A public URL would need access control and spend limits to keep strangers from using the LLM keys, and none of that is required.

## D-013 · 2026-10-14 · Public repository
- **Decision:** The repository is public at `agentic-policy-analytics`.
