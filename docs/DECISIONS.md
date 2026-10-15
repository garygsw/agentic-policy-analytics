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

## D-024 · 2026-10-14 · Triage classifier: measured, then chosen
- **Trial.** 160 labelled questions (70 in scope, 45 vague, 45 off topic), split in half by category. The local classifier trained on one half, and every option was scored on the other 80 (`scripts/triage_trial.py`, results in `backend/data/eval/triage_results.json`):

  | Classifier | Accuracy | Macro F1 | Median latency | Cost per 1,000 |
  |---|---|---|---|---|
  | Local (embeddings + logistic regression) | 87% | 0.86 | 2 ms | $0 |
  | Jev (TypeSafe AI) | 94% | 0.93 | 263 ms | $0.017 (vendor-listed price) |
  | Fast LLM (gpt-6-luna) | 99% | 0.99 | 1.7 s | $0.057 |
  | Local, below 0.6 confidence to the LLM | 98% | 0.97 | 3 ms (p95 2.1 s) | $0.019 |
  | Jev, below 0.6 confidence to the LLM | 96% | 0.96 | 263 ms | $0.023 |

- **Threshold sweep** (local, uncertain cases to the LLM):

  | Threshold | Accuracy | Questions sent to the LLM |
  |---|---|---|
  | 0.5 | 92% | 11% |
  | 0.6 | 98% | 30% |
  | 0.7 | 99% | 43% |

- **Decision:** The default is the local classifier at a 0.6 threshold, with uncertain cases going to the LLM. It is trained on all 160 questions in production. Users can choose local, Jev or the LLM for each request.
- **Why:** It has close to the LLM's accuracy at a third of the cost, and a typical decision takes milliseconds offline. Every local mistake had confidence below 0.63, which is why the threshold catches them.
  - Jev was not chosen as the default. It classified the demo question itself ("Analyse employment trends in the technology sector") as off topic, with confidence 0.58, the same result as an earlier probe. It also sent two vague questions to off-topic with confidence above 0.7.
- **Caveats:** The test set has only 80 questions, so the accuracy figures carry wide margins (several percentage points). The labels were drafted with AI help and need human review. "Am I eligible for an HDB loan?" fooled every classifier, which suggests its label (off topic) is debatable.

- **Re-run after relabelling (2026-10-14).** HDB loan questions stay off topic. Questions only about other countries are off topic, but comparing them with Singapore is in scope. With these labels and examples there are 168 questions (75 in scope, 45 vague, 48 off topic), and 84 are held out:

  | Classifier | Accuracy | Macro F1 | Median latency | Cost per 1,000 |
  |---|---|---|---|---|
  | Local | 92% | 0.91 | 2 ms | $0 |
  | Jev | 93% | 0.93 | 266 ms | $0.018 |
  | Fast LLM | 100% | 1.00 | 2.2 s | $0.058 |
  | Local, below 0.6 to the LLM (default) | 99% | 0.99 | 3 ms (p95 2.4 s) | $0.015 |
  | Jev, below 0.6 to the LLM | 94% | 0.94 | 267 ms | $0.020 |

  At the 0.6 threshold, 27% of questions go to the LLM. The default still holds. The one remaining mistake is "Where does the Minister for Manpower live?", which the local classifier called in scope with confidence 0.64. Jev again called the demo question off topic, this time with confidence 0.73.

## D-025 · 2026-10-14 · MAS not crawled; crawl depth raised to 500 per agency
- **MAS:** For non-browser clients, its site returns the same "service unavailable, try another browser" page for every URL, including its sitemap and any feed path. Getting around that would mean evading the site's access controls, so MAS is not crawled. MAS pages remain reachable through the web search tool (`mas.gov.sg` is in its domain list).
- **Crawl depth:** The default is the 500 most recent pages per agency (changed since 2024). The first crawl took 150 per agency: 749 documents, no failures, plus the 4,655-dataset catalogue, giving 7,592 passages in a 35 MB index.

## D-026 · 2026-10-14 · API: background runs, numbered events, replayable stream
- **Decision:**
  - **Background runs.** FastAPI starts each user message as a background run in a worker thread, because the graph and its LLM calls are synchronous. The API returns at once with a run ID (HTTP 202).
  - **Stored events.** Every trace event is saved with a per-run sequence number. A WebSocket (`/api/runs/{id}/stream?after=N`) replays the saved events, then follows the run live, so a late viewer or a reconnecting browser sees the whole run. Report drafts are stored in chunks of about 120 characters rather than token by token.
  - **Storage.** A second SQLite database (`var/app.sqlite`, SQLAlchemy) lists threads and runs and holds events and the LLM call log. The conversation itself stays in the LangGraph checkpointer (D-002).
  - **Rules.** A thread runs one request at a time; a second message gets HTTP 409. A message or answer containing personal identifiers is rejected with HTTP 400 in the user's language, before it is stored or sent anywhere.
  - **Report security.** HTML reports are served with a Content-Security-Policy. Scripts may only be inline or from cdnjs (Plotly, pinned with SRI), and the page may make no network connections. Plotly charts render under this policy, checked in headless Chrome.
- **Alternatives:**
  - Server-sent events: simpler but one-way, and the scope asked for WebSockets.
  - A Celery or Redis job queue: more infrastructure than one machine needs.
  - Events kept only in memory: no replay after a restart, and no audit trail.
- **Agent fixes found by live API runs:**
  - **Search budget.** Each extraction task may run at most 3 document searches and 2 web searches, enforced in code.
  - **Prompt rule.** Curated datasets are official statistics: the agents do not search to verify them. A curated dataset whose coverage differs slightly from the request (e.g. residents only) is used, and the difference is stated as a caveat.
  - **Retries keep earlier work.** A retried extraction keeps what the first attempt loaded. It was replacing it, which left analytics with no data.
  - **Duplicate web results.** Web results that differ only by a page-version or tracking parameter count as one page.
- **Measured** (the demo comparison question, low effort, through the API):

  | | Time | LLM calls | Cost | Result |
  |---|---|---|---|---|
  | Before | 210 s | 25 | $0.23 | Answered, after redundant searches |
  | With the budget only | 121 s | 23 | $0.11 | Unanswered: the retry lost the loaded data |
  | All four fixes | 89 s | 14 | $0.13 | Answered: grounded, reviewer passed, chart rendered |

## D-027 · 2026-10-14 · "Partial" outcome; cited passages only; yearly changes per trend
- **Decision:**
  - **"Partial" outcome.** A run ends as `partial` when the reviewer's last check still finds the report does not fully answer the question. This happens once revisions run out. The report is still shown, since every number in it is grounded, and it states the reviewer's objection.
  - **Sources list.** Document and web passages appear in a report's source list only when a finding cites them. Datasets are always listed, because facts and charts cite them.
  - **Yearly changes.** For series short enough to list every period, `compute_trend` also returns the year-on-year change for each period.
- **Why:**
  - Calling such a run `answered` misled anyone looking only at the status.
  - The agents read more passages than a report uses, and listing them all made reports noisy.
  - Without the yearly changes, the analytics agent called the tool once per pair of years: ten extra calls in one live run.
- **Alternatives:**
  - Hide reports the reviewer rejects: this loses grounded findings.
  - List every passage read: this is complete but noisy, and the run's full source list remains available from the API.

## D-028 · 2026-10-14 · Frontend: one Next.js page modelled on a coding agent's desktop app
- **Decision:**
  - **Stack.** One client-rendered Next.js 16 page in TypeScript. Plain CSS with light and dark themes. `react-markdown` for chat text, and no UI component library. Dependencies are pinned to releases at least two weeks old.
  - **Live view.** A WebSocket per running run feeds a pure reducer (`lib/runState.ts`). The reducer derives everything shown: the step list filtered by verbosity (brief/standard/full), the one-line status, elapsed working time (pauses for the user are excluded), token and cost counters summed across paused and resumed parts, the streamed draft with its check stage, and each agent's latest context window. The same events, live or replayed from history, give the same view.
  - **Controls.** The per-request options (model, reasoning effort, optional tools, triage method, web scope, plan approval) and the verbosity are kept in the browser's local storage.
  - **Reports.** The HTML report is the API's own page, served with its CSP, in a sandboxed iframe (scripts on, no same-origin access). It posts only its height, so the frame fits it. Exports are the HTML page and the Markdown file.
  - **Inspector.** A side panel shows each agent's context window (tokens by category: system prompt, task, own messages, tool results, retrieved passages, tool definitions; plus the messages sent) and the LLM call log, with input split into uncached, cache read and cache write.
- **API changes for the UI:**
  - Draft events carry readable snapshots of the report so far, parsed from the streamed JSON on the server.
  - The user's replies to clarifying questions and plan approvals are recorded as events.
  - Cancelling a paused run records an end event.
- **Found by driving the UI against live runs, and fixed:**
  - A second analysis step (after reviewer feedback) replaced the first one's facts and charts, leaving a report with no figures. Later steps now build on earlier ones and continue the F#/C# numbering.
  - HDB changed its town names from "Ang Mo Kio" to "ANG MO KIO" in 2019, which split every town's price series in two. Labels that differ only in letter case are now merged during cleaning, and the merge is recorded in the quality report.
  - The quality summary said "722,376 clean rows from 242,634". That is one row per measure in the tidy format, but an agent read it as data duplication. It now says "values (one per measure per row)".
- **Alternatives:**
  - Server-rendered pages: a live trace needs client state anyway.
  - Tailwind or a component library: more to explain for little gain on one page.
  - Rendering the report in React from structured JSON: it would duplicate the HTML template the API already serves and exports.
- **Testing:** Unit tests cover the reducer and formatting (vitest). The page was checked end to end in headless Chrome against the live API: history, the inspector, a personal-data refusal, two live questions, a clarification then a resume then a stop, phone width and dark mode.

## D-029 · 2026-10-14 · Ranking tool, one reviewer send-back, and planning with published figures
- **Decision:**
  - **Ranking tool.** A new analytics tool, `rank_changes`, ranks groups (towns, industries) by their rise or fall between two periods in one call. Every group is compared over the same two periods; groups missing either period are left out and named. It ranks by percentage or absolute change, highest or lowest first, and records the start value, end value and both changes as facts for the groups it shows.
  - **Reviewer limit.** The reviewer may send the analysis back once per question. A second objection is shown with the report, and the run ends as `partial` (D-027).
  - **Coordinator prompt.** The coordinator's prompt now says what the analysis can and cannot do. It works with published series and cannot compute new statistics, such as medians, from individual records. So it plans with published figures and states those choices as caveats.
- **Measured** on "Which HDB towns saw the largest rise in median resale prices since 2020?" (low effort, through the API):

  | | Time | LLM calls | Cost | Result |
  |---|---|---|---|---|
  | Before | 245 s | 31 | $0.31 | Partial: one trend per town, and the reviewer looped |
  | Ranking tool and reviewer limit | 166 s | 26 | $0.12 | Partial: the coordinator chased raw transactions |
  | Plus the coordinator prompt | 100 s | 15 | $0.14 | Answered: matched-quarter rankings by flat type, reviewer passed |
- **Alternatives:**
  - A median-from-records tool: rejected for now. It would recompute official statistics differently from the publisher, and the published medians answer the question.
  - Raising the step limit: this makes runs slower and costlier without fixing the cause.

## D-030 · 2026-10-14 · Evaluations: retrieval, answers and LLM-as-judge
- **Decision:** The evaluations are implemented in the project itself (`app/evals/`), using standard metrics. Results are saved in `backend/data/eval/`, and method and results are described in `TESTING.md` (first written as `docs/EVALUATION.md`).
  - **Retrieval** is scored with recall@1, @5 and @10 and MRR@10, on a golden set of 50 questions generated from sampled passages. The source document is the relevant result.
  - **Answers** are scored end to end on 11 questions:
    - outcome against the expected one
    - groundedness of numbers
    - drafts rejected by the report check
    - citation coverage
    - time and cost
    - a judge from the other provider (relevance, completeness, faithfulness, clarity, caveats)
    - consistency over three repeated runs
- **Results (14 Oct 2026):**
  - **Answers.** 11 of 11 outcomes as expected; 227 of 227 numbers grounded; every finding cited. Judge means: relevance 4.9, completeness 4.1, faithfulness 3.6, clarity 4.6, caveats 3.9. Consistency 0.8. Median 104 s per answered question; $1.94 for the whole evaluation.
  - **Retrieval.** Hybrid search has the best MRR (0.74). The cross-encoder reranker lowers it to 0.64 and adds 0.5 s per search.
- **Changed because of the evaluation:**
  - The coordinator now sees the curated dataset titles.
  - The judge now sees every cited fact, dataset caveats and cited passages.
  - Two eval questions were reworded or given wider expectations where the data does not fit them.
- **Open:** whether to switch the reranker off by default, and how to check qualitative claims (faithfulness is the weakest score). Both await a decision.
- **Alternatives:**
  - Evaluation frameworks such as RAGAS or DeepEval: they add dependencies, and their metrics are harder to explain line by line than the ones used here.
  - Human labelling: better, but not feasible in the time available; the retrieval labels and question set are open to review.

## D-031 · 2026-10-14 · Reducing hallucination in prose; reranker off by default
- **Decision:**
  - **Tighter report prompt.** Every statement, caveats included, must be supported by the facts, the source notes and quality reports, or the passages. No background knowledge: no definitions, dates, reasons or descriptions of the data that the material does not state.
  - **Caveats carry citations.** Caveats now cite their sources, as findings do, and the report check covers them: numbers grounded, citations present and known. Reports saved earlier with plain-text caveats still load.
  - **What a claim may draw numbers from.** A claim may use numbers from the notes and quality report of a dataset it cites, as well as from passages it cites.
  - **Report check rules.** The check scales a fact by its unit ("S$5,845.3 million" for a fact of 5,845.3 in S$ million), ignores the sign ("fell 6.8%" for −6.8), and accepts chart IDs as citations.
  - **Coordinator's view of progress.** The coordinator's progress view now includes what is recorded in the state: the datasets loaded and the number of facts computed. Both prompts say that extraction never returns figures.
  - **Reranker.** Cross-encoder reranking is off by default (`RAG_RERANK=false`; available per search with `/api/search?rerank=true`).
- **Measured** (answer evaluation, before and after): judge faithfulness 3.62 → 4.38 and caveats 3.88 → 4.75. Outcomes as expected 11/11 → 10/11; the miss was COE ending "partial" on reviewer feedback, after it answered in the two previous runs. Numbers grounded: 100% in both. Cost per run: unchanged. Reranking: MRR 0.74 without it versus 0.64 with it, and 0.5 s less per search (`TESTING.md`).
- **Why:** numbers were already guaranteed by code; prose was not. Requiring citations on every statement and checking them in code extends the same guarantee to caveats. The prompt rule covers what code cannot check: whether a sentence means what its sources say.
- **Alternatives:**
  - A claim-by-claim LLM verifier: deferred. It adds a call to every question, and the cheaper changes closed most of the gap.
  - Keeping the reranker, or blending it into the fusion: neither scored better than plain hybrid search on the golden set.
- **Trade-off:** ignoring signs means a report that says "rose" for a fall still passes the number check. Direction is wording, left to the reviewer and the judge.

## D-032 · 2026-10-15 · Docker images, Compose and CI
- **Decision:**
  - **Two images.** The API image (`backend/Dockerfile`) is Python 3.12 slim with uv and the locked dependencies, run as an unprivileged user. It also runs the ingest service, with a different command. The web image (`frontend/Dockerfile`) is Next.js's standalone server on Node 24. Base images and tools are pinned to exact versions at least two weeks old.
  - **Compose.** `docker-compose.yml` runs three services: `api`, `ingest` (the cron scheduler) and `web`. All three share `./var` with local development, which holds the document index, caches, models, conversations and reports. Keys come from `.env`, which never enters an image: the root `.dockerignore` admits only `backend/`.
  - **CI.** GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request:
    - backend lint and tests, with coverage
    - frontend type check, tests and build
    - both images built and started together, with a smoke test of the API's health and data endpoints and the web page
  - **Publishing.** On a version tag (`v*`), the images are pushed to the GitHub Container Registry.
- **Alternatives:**
  - Baking the embedding models and document index into the image: a much larger image, and stale until rebuilt. They are created at runtime in the mounted `var/` instead.
  - Running ingestion inside the API process: one container fewer, but a crawl would compete with live requests. A separate service also matches D-006.
  - Third-party actions for building and publishing images: the plain `docker` CLI does the same with fewer dependencies to trust.
- **Limit:** the web image fixes the API address when it is built (`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`), so it suits running on one machine. Hosting it elsewhere means rebuilding with the right address.

## D-033 · 2026-10-15 · The internal source is a synthetic Excel workbook, not a mock JSON API
- **Decision:** The "internal system" is the synthetic workbook `backend/data/internal/tech_talent_programmes.xlsx`, read through the same source interface as the public APIs. This revises D-005 and D-014, which planned a mock JSON API.
- **Why:**
  - The brief's formats are already covered: CSV (data.gov.sg downloads), Excel (the workbook) and JSON APIs (data.gov.sg, SingStat).
  - The workbook carries the problems an internal export typically has (a duplicate, a missing value, a non-numeric value, total rows), and the cleaner reports them.
  - If the file is missing, the source fails like a live API and the same fallback applies.
- **Alternatives:** A small JSON API serving the same data, with a failure switch. It is closer to a real internal system, but adds code without showing anything new, and the time went to documentation.

## D-034 · 2026-10-15 · Never drop content; search before declining; whole-web search; Claude Sonnet 5.5 default; simpler chat bar
Changes from the first round of hands-on testing.
- **Decision:**
  - **Flag, never drop.**
    - A statement that still fails the number check after two fixes stays in the report, marked ⚠ with what could not be verified. It is no longer replaced by the template of computed figures.
    - The reviewer's remaining objection is likewise a flag.
    - Every report ends as `answered`, with its flags listed. The `partial` outcome (D-027) is removed.
    - The template is now used only when the models fail before any report exists.
  - **Check messages quote their context.** Each problem sent back to the writer quotes the words around the number. A statement citing a source may also use that source's dates (when it was published or retrieved).
  - **Search before declining.** The coordinator cannot decline until at least one extraction step has searched documents and the web. A decline before that is turned into a search. The prompt adds that a policy announced to take effect in the future is not a forecast.
  - **Web search.** It covers the whole web; the government-only option is removed. The budget is 10 searches per extraction task (was 2), and the extraction agent's step limit is 10 (was 6). The agents are told to prefer official sources and to say when a source is a third party.
  - **Default model.** Claude Sonnet 5.5 is the default main model, with GPT 6.1 Sol as its fallback. This revises D-002, where OpenAI was primary; the fast tier is unchanged. The UI's model menu offers Claude Sonnet 5.5 and GPT 6 Luna by name. The API and CLI accept all four models.
  - **Chat bar.**
    - A + menu on the left holds a switch per optional tool, plan approval and the triage method.
    - The model menu sits beside the send button, with an Effort submenu.
    - The Detail levels are named Normal (the default), Thinking and Verbose.
- **Why, from testing:**
  - "Will petrol cars be phased out by 2030?" was declined with no search at all. One search finds the announced policy.
  - A follow-up asking the system to check the internet fell back to the template. One caveat said the data was "retrieved 2026-10-14", and the check read the 14 as an unmatched number. So every finding about the policy was discarded over a date.
- **Trade-offs:**
  - **Flags.** They put the judgement on the reader. A flagged statement is visibly unverified, and the report's header says so.
  - **Searches.** A search before every decline costs one extraction step, roughly $0.02–0.05, on questions that will be declined anyway.
  - **Whole-web results.** They can come from weaker sources. They stay labelled untrusted, and the writer must cite them.

## D-035 · 2026-10-15 · Claim checker, report downloads, conversation titles, simpler controls
- **Decision:**
  - **Claim checker.** The reviewer now also checks every statement (summary, findings, caveats) against the facts and sources it cites, and returns the unsupported ones. It covers:
    - comparisons and rankings ("largest", "alone", "only")
    - direction (rose or fell, which the number check ignores)
    - periods and definitions
    - causes and attribution

    Unsupported statements go back to the writer, sharing the limit of two fixes; any that remain are flagged ⚠ (D-034). The review runs on a new "check" tier: the other provider's model, with the main model as its fallback, so a model never checks its own writing. It is one call, the reviewer's existing one, with the writer's material added.
  - **Downloads.** When a run finishes, the API builds a Markdown file and a PDF from its final state and stores both with the run.
    - The Markdown embeds each chart as an image, with its data table.
    - The PDF is built with fpdf2. Its citations jump to the fact or source, source addresses open in a new tab, and flagged statements keep their warnings.
    - Chart images are drawn with matplotlib from the same Plotly specs as the HTML report. Both libraries are pure Python.
    - The PDF uses the DejaVu font that comes with matplotlib, which covers Latin script. Chinese and Tamil reports therefore get an English PDF, which the PDF says.
    - The new database columns are added to existing databases automatically.
  - **Report in the chat.** It has no scrollbar of its own, and "Open report" is gone. A citation click scrolls the conversation to the target, opening the facts table when needed. The report asks the page to scroll, because its frame never scrolls.
  - **Titles.** A conversation gets a 3–6 word title, written by the fast model in the question's language. Until it arrives, or if it fails, the question is shown cut at a word boundary. Existing long titles are renamed once, at start-up.
  - **Controls.**
    - Plan approval is a mode, Plan or Auto (the default), beside the + menu.
    - The + menu holds only tool switches.
    - Triage is a server setting, with no UI control.
    - The sidebar can be hidden. It is visible by default, and the choice is remembered in the browser.
- **Measured:** see TESTING.md.
- **Alternatives:**
  - **A separate claim-check node and call:** a little clearer, but an extra call per draft; the reviewer already reads the report.
  - **Browser print-to-PDF:** exact and multilingual, but not a one-click file.
  - **HTML-to-PDF with WeasyPrint or headless Chrome:** needs system libraries or a browser in the image.


## D-036 · 2026-10-15 · Docker state in a volume; results saved before the end event
- **What happened:** during QA, `var/app.sqlite` (the history of runs, events and LLM calls) was corrupted. Its first page was overwritten with zeros.
  - The Compose stack bind-mounted `./var` from the Mac into Colima's Linux VM.
  - SQLite in WAL mode coordinates processes through a shared-memory file, which cannot be shared across that boundary.
  - So a process on the Mac (here, `sqlite3`, used for debugging) reading the database while a container wrote it was enough to break it. The column migration then ran on the damaged file.
- **Recovered:** the checkpoints and the document index were intact. From `app.sqlite`, SQLite's recovery and a rebuild by table restored all 14 conversations, 9 of 12 runs, 1,628 events and 180 LLM calls. The damaged file and a backup are kept outside the repo.
- **Decision:** Docker keeps its runtime state in a named volume, `state`, inside the VM, so no host process can open its databases. Local development keeps `var/` on the Mac, and the two no longer share data. The Compose file shows how to seed the volume from a local `var/` once, with everything stopped. This replaces the shared `./var` of D-032.
- **Also fixed:** a run now saves its results (answer, report, downloads) before its "end" event. Previously, a client that fetched the run on "end" could arrive before the report was saved, and showed the answer without it. Building the downloads made that window about a second long. A WebSocket follows a run until it has sent "end" and the run is no longer running, so the reordering is safe for live viewers.
- **Alternatives:**
  - Keep sharing `./var` with a rule not to open the files while Docker runs: easy to break by accident, as happened here.
  - Use SQLite's rollback journal instead of WAL: it relies on file locks across the VM boundary, which are not reliable either.

## D-037 · 2026-10-15 · A rejected plan brings another plan; the claim checker is a server setting
- **Decision:**
  - **Plan mode.** Rejecting a plan no longer cancels the run. The coordinator drafts a different plan, using the user's feedback if given, and pauses again for approval. The plan card offers Approve, "Revise the plan" (with feedback) or "Suggest another plan" (without), and a separate Cancel, which stops the run through the API. The graph's cancel step is removed, as nothing leads to it.
  - **Claim checker setting.** It can be switched off with `CLAIM_CHECK=false` (default on). There is no UI control. Off, the reviewer gets the short verdict-only prompt and none of the writer's material, so it costs what it did before D-035.
- **Why:**
  - In testing, a rejection ended the run with "cancelled at your request", when the user wanted to see another plan.
  - Cost: an answer evaluation run costs about $3.80 with the claim checker and $2.60 without.

## D-038 · 2026-10-15 · Clarifying questions offer answers; conversations can be pinned, renamed and deleted
- **What happened:** in testing, a clarifying question said "Reply in the box below", but the box was the chat bar at the bottom of the page, far from the question, and was easy to miss.
- **Decision:**
  - **Clarifying questions.** The fast model now writes the question together with two to four short answers, as structured output (at most four are kept). The question card shows them as buttons, with its own box below them for a different answer, and a Cancel button. The chat bar is disabled while the agents wait, as it is during plan approval, so there is one place to answer. In the terminal, an answer can be picked by its number.
  - **Conversation menu.** Each conversation in the sidebar has a ⋮ menu: Pin (pinned conversations are listed first, under their own heading), Rename (edited in place), and Delete (asks for confirmation in the page). The keys P, R and D choose an item while the menu is open. The "New analysis" button is now "+ New".
  - **Rename** marks the conversation as renamed, so the background title writer (and the start-up retitling of long titles) never replaces a name the user chose. Neither renaming nor pinning moves a conversation among the recent ones.
  - **Delete** is permanent. It removes the conversation, its runs, their trace events and LLM call logs, and its LangGraph checkpoints. It is refused while a run is working, since that run would keep writing.
  - Two new columns (`pinned`, `renamed`) are added to existing databases by the start-up column check, which now applies column defaults to existing rows.
- **Alternatives:**
  - **Keep answering in the chat bar, with the choices in the card:** two places to answer one question.
  - **Soft delete (hide, keep the data):** keeps the audit trail, but the user asked for Delete, and a hidden record of a question the user removed is surprising. The LLM call log of a deleted conversation goes with it.
  - **Pins kept in the browser only:** simpler, but pins would not follow the user to another browser and the API could not order the list.

## D-039 · 2026-10-15 · Deletes are soft deletes; conversations can be searched
- **Decision:**
  - **Soft delete.** Deleting a conversation sets `threads.deleted_at` and nothing else. The conversation, its runs, their trace events, the LLM call log and the LangGraph checkpoints are kept. Every API route treats it as unknown (404), including its runs and reports, and the history list and search leave it out. This replaces the permanent delete of D-038.
  - **Restoring** is a backend task, not a UI feature: `python -m app.admin deleted` lists deleted conversations and `python -m app.admin restore ID` brings one back. In Docker it runs inside the API container (`docker compose exec api …`), so the database is never opened from the host (D-036).
  - **Search.** The sidebar's search box asks the API (`GET /api/threads?q=…`), which matches the words in a conversation's title or in any question asked in it, ignoring letter case. `%` and `_` are matched literally. The box waits for a 200 ms pause in typing, and a slower, older reply never replaces a newer list.
  - **Clarifying questions** list "Other" as the last numbered answer, with its own box, so writing your own answer reads as one more choice.
  - **New** has an icon and a shortcut, ⌘⇧O or Ctrl+Shift+O (as in other chat apps; Ctrl+N and Ctrl+T belong to the browser). The tools menu no longer lists the tools that are always on.
- **Why:** a deleted conversation should be recoverable, for mistakes and for audit. Searching questions as well as titles finds a conversation by what was asked, even after it has been renamed.
- **Trade-offs:**
  - Deleted data is never removed. A real deployment needs a retention rule: purge conversations deleted more than N days ago, and erase at once on a data-protection request. That purge would be a further `app.admin` command; it is not built.
  - Search uses SQL `LIKE` over at most a few hundred conversations. At scale it would use the existing FTS5 machinery or the vector index instead.
- **Alternatives:** restore through an API route (it needs an administrator role, which needs authentication first); search only the loaded titles in the browser (instant, but misses questions and anything past the first 100 conversations).

## D-040 · 2026-10-15 · No authentication in this version; the production design is documented instead
- **Decision:** the API and UI stay without a login. ARCHITECTURE.md (section 10) describes how sign-in, ownership, roles and auditing would be added, and the README warns against running the stack on a shared network.
- **Why:**
  - The brief does not require authentication; it raises security as a question for the innovation essay.
  - Little time remains before the code freeze. A login layer touches every route, the tests, the load test, the CI smoke test and the demo itself.
  - A half-built login (a single shared key) would protect little while suggesting more.
- **Constraint for a later design:** the report frame, the download links and the WebSocket are fetched by the browser itself and cannot carry an `Authorization` header. So a session cookie (HttpOnly, SameSite) fits this app better than bearer tokens.
- **Alternatives considered:**
  - **A shared access key** behind a login screen, setting a cookie (2–3 hours). It stops strangers from spending the LLM budget, but there is no notion of users.
  - **Local accounts** with hashed passwords, per-user conversations and an admin role (about a day). It is real authentication and authorisation, but with passwords stored here rather than single sign-on.
  - **Single sign-on** through an identity server (Keycloak in Compose, or a cloud directory). This is the production answer, but too heavy for reviewers to run.

## D-041 · 2026-10-15 · Runs cut off by a restart continue from their last step
- **What happened:** during QA the API container was rebuilt while a run was searching the web. Runs execute in worker threads inside the API process, so the run died. The database still said "running", nothing ever closed it, and the page waited for events that never came. Stop could not end it either: it sets a flag that only a live worker reads.
- **Decision:**
  - When the API starts, every run still marked "running" continues from its last LangGraph checkpoint. The step that was cut off runs again from its start; earlier steps do not. The trace records "Server restarted: continuing from the last saved step".
  - A run is continued once. If it is cut off again, or its checkpoint has nothing left to run, it ends as failed with "The server restarted while this analysis was running. Please ask again." A run that kills the server cannot loop.
  - Stop closes a running run directly when no worker in this process is executing it.
  - Verified on the stuck run: after the API restarted, it continued from its extraction step and finished with a report about two and a half minutes later.
- **Alternatives:**
  - **End such runs as failed** (simplest), and the user asks again: all the work done before the restart is lost.
  - **Mark them interrupted, with a Continue button:** nothing spends money without the user, but it needs more UI, and in practice the user wants the answer.
- **Limits:** this assumes one API process, which runs every run. With several processes or separate workers, each run would need a lease renewed by its worker, so another process continues only runs whose owner has stopped. The usage of the part of a run that was cut off is in the LLM call log but not in the run's totals.
