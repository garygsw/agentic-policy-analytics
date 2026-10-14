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
- **Resolved in D-023.**

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
- **Personal data:** Identifiers are blocked: NRIC/FIN, phone numbers, email addresses, card numbers, bank accounts, and street addresses with unit numbers. Names are not blocked, because policy questions legitimately name ministers and MPs. The screen runs in the API before the graph starts, because LangGraph checkpoints its input.
- **Languages:** Singapore's four official languages (English, Chinese, Malay, Tamil). Only report prose is translated; source titles, tables and citations stay as published. If a translation changes any number, the user gets the English report with a note.
- **Plan approval:** Optional, and off by default. Clarifying questions for vague queries are always on.
- **Fast classification:** See D-017.

## D-011 · 2026-10-14 · Evaluation with standard metrics
- **Decision:**
  - Groundedness: numbers are checked against computed values, and claims against the passages they cite.
  - Retrieval: recall@k and MRR on a labelled query set.
  - Subjective quality: an LLM-as-a-judge with a written rubric.
  - Consistency: the same question asked repeatedly.
  - Guardrail classifiers: precision and recall.
- **Implementation:** Our own code, following the standard metric definitions. It is short, has no extra dependencies, and is easy to explain.

## D-012 · 2026-10-14 · No hosted demo
- **Decision:** No public live site. Reviewers run the stack locally with Docker Compose, and the README will include a demo video once the app is complete.
- **Why:** A public URL would need access control and spend limits to keep strangers from using the LLM keys, and none of that is required.
- **Release:** CI runs the tests and builds the Docker images on every push. A version tag also publishes the images to GitHub Container Registry, so a release can be run without building it.

## D-013 · 2026-10-14 · Public repository
- **Decision:** The repository is public at `agentic-policy-analytics`.

## D-014 · 2026-10-14 · Data sources: scope
- **Decision:**
  - **Structured data** comes through the data.gov.sg API, which covers 4,655 datasets from 74 agencies. Examples: COE bidding results, HDB resale prices, CEA transaction records, URA private-property data, new-flat bookings, the Master Plan 2025 rail-station layers, MOH and IMDA statistics, and employment by industry. SingStat's Table Builder API and a mock internal API add to this.
  - **Documents** come from agency newsrooms and statistics pages whose robots.txt allows crawling: MOM, MOF, PMO, MAS, MOH and URA. They are added through one configurable connector.
  - **Web search** is a separate tool for recent context.
- **Excluded:**
  - Parliamentary Hansard: its search API returns errors and the parliament site blocks automated access.
  - Commercial news: copyright.
  - Sites that block automated access: gov.sg, MOE, HDB and Statutes Online.
  - Services that need a separate registered key (LTA DataMall, URA's data API, OneMap): data.gov.sg already carries their datasets.
- **Why:** One catalogue API covers the whole of government with consistent metadata (size, columns, coverage), and every source is used within its access rules.

## D-015 · 2026-10-14 · LLM layer (tiers confirmed)
- **Decision:**
  - LangChain chat models arranged in two tiers, each with an OpenAI primary and an Anthropic fallback: **quality** (`gpt-6.1-sol`, then `claude-sonnet-5-5`) and **fast** (`gpt-6-luna`, then `claude-haiku-5-5`). The models in each tier cost the same per token, so a fallback does not change the cost.
  - Provider endpoints are set in the app's own config, so ambient shell variables cannot redirect calls.
  - Prices come from the official pricing pages and are dated in `app/llm/pricing.py`. A model without a listed price is reported as unpriced, never estimated.
- **Alternatives:** One model per provider for everything. Hosted embeddings for retrieval (rejected in D-006).

## D-016 · 2026-10-14 · Web search tool
- **Decision:** Tavily, with a free plan of 1,000 searches a month and no card required.
  - **Scope:** limited to `*.gov.sg` domains by default.
  - **Trust:** results are treated as untrusted data, never followed as instructions, cited with the URL and retrieval date, and labelled as web sources in the report.
  - **Key check:** done through the free usage endpoint.
- **Alternatives:** Exa (one-time credit). Brave (free plan status unclear). DuckDuckGo (no key, but unofficial and fragile).
- **Why:** It is the simplest integration, the free plan resets monthly, and it can be restricted by domain.

## D-017 · 2026-10-14 · Fast classification decisions
- **Decision:** A local classifier makes the fast routing decisions (on-topic, off-topic, vague, unanswerable). It reuses the embedding model the RAG pipeline already loads and is trained on a labelled query set. Low-confidence cases go to the fast LLM tier.
  - **Trial:** Jev, TypeSafe AI's hosted decision model, is compared with the local classifier and the fast LLM on the same labelled set: accuracy, latency and cost.
- **Alternatives:**
  - NanoJev, an open replica of Jev: it is trained only on game tasks and would need fine-tuning first.
  - A zero-shot NLI classifier: needs no training data, but is slower.
- **Why:** The decisions are fast, free and offline, and they are measured against a hosted decision model and an LLM rather than assumed.

## D-018 · 2026-10-14 · Structured data access
- **Decision:** Two paths:
  - **Curated:** about 18 datasets with known schemas, tested cleaning rules and committed small snapshots, used for the demo and tests.
  - **Discovery:** any dataset in the data.gov.sg catalogue can be found and loaded with generic profiling, labelled "uncurated", and accompanied by a data-quality report.
  - **Large files:** files such as the 24 MB resale-price data are downloaded on first start and never committed.
  - **Licence:** each dataset's licence is checked before any snapshot is committed.
- **Why:** Curated datasets make the demo reliable; discovery covers the whole of government.

## D-019 · 2026-10-14 · Analytics: computed facts, with aggregation rules enforced in code
- **Decision:**
  - **No LLM arithmetic.** The agents never calculate numbers. They choose what to compute, and pandas tools compute it.
  - **Numbered, cited results.** Every result is recorded as a numbered fact (`F1`, `F2`, …) linked to its source dataset (`S1`, `S2`, …). Charts (`C1`, …) are drawn from the data by reference, never from numbers an LLM wrote.
  - **Aggregation rules in code.** Each curated dataset declares which measures may be added up, and across which dimensions. The tools refuse anything else: medians, prices, indices, nested GDP series and overlapping industry labels. The error tells the agent which value to filter by instead.
  - **Correlations report two numbers.** One is for the levels and one for the period-to-period changes, so a shared upward trend is not mistaken for a relationship.
  - **Visible reasoning.** Every tool call carries a `reason`, which is shown in the live trace.
  - **Agent loop.** The Extraction and Analytics agents run a ReAct tool loop with a step limit inside their graph nodes. Tool errors are fed back to the agent as observations.
- **Alternatives:**
  - The LLM writes SQL or pandas code. This is more flexible, but it needs a sandbox and makes every number harder to check.
  - Rules about adding up stated only in the prompt. This proved unreliable in public prior art.
- **Why:** Numbers are correct by construction and traceable to their source, and every step can be explained.

## D-020 · 2026-10-14 · User-controlled run options and live trace
- **Decision:**
  - **Per-request options.** Each request can set three things:
    - the main model: any priced model. Choosing a Claude model makes the OpenAI model at the same price its fallback, and the other way round.
    - the reasoning effort: low, medium or high, default low. It applies to both providers; triage and translation always run at low.
    - the enabled optional tools: catalogue search, correlation, record listing and charts. Data loading and the core statistics are always on.
  - **Where the options live.** They travel in the run's config, not in checkpointed state.
  - **One event format for the live trace,** with three levels shown according to the user's verbosity setting:
    - 1, brief: status one-liners, actions and errors
    - 2, standard: adds reasoning and tool results
    - 3, full: adds model thinking and context snapshots
  - **Two events after every LLM call:**
    - a running usage total (calls, tokens, cached tokens, cost), from which a client builds the elapsed-time, token and cost counters
    - a context snapshot for that agent: the token breakdown by category, the share of the model's context window, and the messages themselves.
- **Thinking display:** The agents' own reasoning is always shown: the coordinator's thought, the reason on each tool call, and the triage and reviewer explanations. Provider-native thinking is shown when a provider returns it. In tests, OpenAI reasoning summaries and Claude thinking blocks were often empty during tool calls, so they cannot be the main display.
- **Why:** The user can see and control what the agents do, how hard they think, what it costs, and what each agent was given.
- **Measured:** On the demo question, effort low took 83 s and $0.07. The model default took 140 s and $0.14.

## D-021 · 2026-10-14 · Occupation-based tech workforce data
- **Decision:** IMDA's *Employed Tech and Media Professionals* series (published through SingStat; 2017 to 2025) is curated, alongside MOM's industry-based employment. The agents report both readings of "technology sector" (the industry, and tech jobs across all industries) as separate series, never combined.
- **Why:** The two measures answer different questions, and the demo question is ambiguous between them.

## D-022 · 2026-10-14 · HTML report and streaming
- **Decision:**
  - **Streaming (option A).** The report streams to the user while it is being written, under a "Checking numbers…" label. It is then marked "✓ Checked", or "Revised after check" when the check sends it back to the writer.
  - **Translation.** The checked report is translated field by field into the user's language. Citations and chart IDs stay as they are. If any translated text changes a number, the whole report stays in English.
  - **The HTML page.** It is self-contained, light and dark, and covers:
    - the summary, findings with citation chips, charts and caveats
    - the sources, with agency, link, licence, retrieval date and data-quality summary, plus any fallback warning
    - a collapsible table of every computed fact, where each citation opens its row
  - **Charts.** Plotly specs are drawn by plotly.js 4.1.2 from cdnjs, with a subresource-integrity hash. Each chart's data table is always in the page, so the numbers stay readable offline.
  - **Safety.** All model-written text is escaped, and chart titles are kept plain. Fixed page labels in four languages live in `app/report/labels.json` for translators to review.
- **Alternatives:** Showing the report only after the check: nothing unchecked is visible, but the text appears about 10 to 15 seconds later. Letting the LLM write HTML directly (rejected in D-007).
- **Why:** The user sees progress at once, and verification stays visible instead of hidden.

## D-023 · 2026-10-14 · Document search (RAG)
- **Decision:**
  - **Sources.** Five agency newsrooms, crawled through their XML sitemaps: MOM, MOF, PMO, MOH and URA. The first crawl takes recent pages (changed since 2024, at most 150 per agency). Later runs fetch only new or changed pages, using the sitemap's lastmod. The data.gov.sg dataset catalogue is indexed in the same store.
  - **Crawling.** robots.txt is honoured, including its Crawl-delay. Page text is extracted with trafilatura. Raw HTML is not kept, and the index stays on the machine that built it. It is never committed, because agency terms do not allow republication.
  - **Chunking by document type.** Paragraphs are packed into passages of about 350 tokens, overlapping by one paragraph, and long paragraphs are split at sentence ends. Every passage carries a header: the title, plus the question for parliamentary replies.
  - **Storage.** One SQLite file holds three things: FTS5 for BM25 keyword search, sqlite-vec for vectors, and the documents table with each run's log. WAL mode lets the ingest service write while the API reads.
  - **Models.** Embeddings use bge-small-en-v1.5 (local, MIT licence, 384 dimensions). The index records the model's name and refuses to run with a different one. Reranking uses a local cross-encoder, ms-marco-MiniLM-L-6-v2 (Apache-2.0).
  - **Search.** Keyword, vector and hybrid modes. Hybrid merges the keyword and vector rank lists by reciprocal rank fusion (k = 60), then reranks the top 30, with at most two passages per document.
  - **Agent tools:**
    - `search_documents` returns passages D1, D2, …
    - `web_search` uses Tavily with an explicit list of gov.sg domains (Tavily's domain filter ignores wildcards). The whole web is available only if the request asks for it. Results are W1, W2, …, are de-duplicated, and are marked untrusted.
    - `search_datasets` now uses hybrid search over the catalogue.
  - **Grounding.** A number in the report must match a computed fact, or appear in a passage that the same finding cites.
  - **Scheduling.** The ingest service runs as its own process on a cron schedule (APScheduler, INGEST_CRON, default 03:00 Singapore time).
- **Alternatives:** Hosted embeddings, which would have no fallback provider. A separate vector database service. LLM reranking.
- **Why:** Everything runs locally with no extra service or key, keyword and semantic search complement each other, and every passage is citable and checkable.
- **Measured:** On "AI skills training", keyword search and vector search returned different top documents, and hybrid search with reranking combined both.
