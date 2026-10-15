# Testing

How the system is tested, how hallucination is detected and measured, and the results. Everything here can be
re-run from `backend/` (and `frontend/` for the UI tests). Results are saved in `backend/data/eval/`.

| Level | What it checks | Count | Command | Cost |
|---|---|---|---|---|
| Unit tests | agents, tools, statistics, data cleaning, guardrails, LLM layer, RAG, report check | 175 | `uv run pytest tests/unit` | free (fake models) |
| Integration tests | the whole graph and API: background runs, pauses, streaming, replay, concurrency | 12 | `uv run pytest tests/integration` | free (scripted models) |
| Frontend tests | the event reducer behind the live view, formatting | 7 | `npm test` (in `frontend/`) | free |
| Live provider tests | real calls to both providers, structured output, fallback | 3 | `uv run pytest -m live` | cents |
| Retrieval evaluation | recall@k and MRR for each search mode | 50 questions | `uv run python -m app.evals.retrieval run` | free |
| Triage evaluation | accuracy, F1, latency and cost of each classifier | 84 questions | `uv run python scripts/triage_trial.py` | cents |
| Answer evaluation | outcomes, groundedness, citations, LLM-as-judge, consistency | 11 questions | `uv run python -m app.evals.answers` | about $2 |
| Load test | latency and throughput under concurrent clients | 20 clients | `uv run python scripts/load_test.py` | free |

`uv run pytest` runs the unit and integration tests together (187), with no network access and no API keys. CI
runs them on every push (section 7).

## 1. Test plan

The language models are the part that cannot be unit-tested, so the design keeps them at the edges:

- **Everything else is deterministic and tested with fake models.**
  - **Fakes.** `app/llm/fake.py` provides scripted chat models that return the replies a test sets, through
    the real provider wiring. Usage logging, fallback and structured output therefore run exactly as in
    production.
  - **Workflow tests.** Graph tests drive every route with scripted replies: answer, clarify, decline,
    off-topic, revise, fall back, cancel, pause and resume.
  - **Code checks.** The statistics, cleaning, summing rules and report check are pure code with their own unit
    tests.
- **The models' behaviour is measured, not asserted.** Evaluations run the real system on fixed question sets
  and score it with standard metrics: recall, MRR, groundedness, citation coverage, LLM-as-judge scores and
  consistency. Results are saved, so each change can be compared with the last.
- **Failures are tested as paths, not exceptions.**
  - provider outages
  - source outages
  - missing data
  - ungrounded drafts
  - failed translations
  - personal data
  - ambiguous questions

  Each failure has a defined route and a test that takes it.

## 2. What the tests cover

| Requirement | Where it is tested |
|---|---|
| Agents and tools | `test_analysis.py` (23): every analytics tool, summing rules, error hints, the tool loop and its step limit, extraction then analysis with shared source IDs, retries keeping earlier results, `rank_changes` |
| API endpoints | `test_api.py` (11): health and options, background runs, numbered events, WebSocket replay, reports and their security policy, personal-data rejection, clarification pause and resume, cancelling, invalid input, search and datasets |
| Multi-agent workflows | `test_graph.py` (22): the full graph with scripted models (coordinator, extraction, analytics, report writer, number check, reviewer, translation) across every route, including a conversation surviving a restart |
| Hallucination detection | `test_grounding.py` (7) and the report-check tests in `test_graph.py`; measured by the answer evaluation (section 3) |
| Accuracy and consistency | the answer evaluation: expected outcomes, judge scores, overlap between repeated runs |
| Data quality validation | `test_data.py` (33): period normalisation, number parsing, placeholders, duplicates, total rows, case-merged labels, personal columns dropped, rail-station names matched across layers, the internal workbook's planted problems, every committed snapshot loading |
| Error handling | `test_llm_layer.py`: provider failure and fallback, including structured output. `test_data.py`: retries, then stale cache, then snapshot. `test_graph.py`: model failure leads to the template report; an unanswerable question is declined; a vague one gets a clarifying question |
| Guardrails | `test_pii.py` (19): NRIC/FIN, emails, phones, unit numbers, bank accounts, cards (Luhn); names and statistics allowed. `test_language.py` (9). `test_triage.py` (8) |
| Retrieval | `test_rag.py` (24): chunking, crawling rules, incremental re-crawls, each search mode, fusion, filters, per-document cap, web result de-duplication, concurrent searches |
| Performance and load | `test_load.py`: 20 concurrent runs through the API with no lost events. `scripts/load_test.py`: HTTP load (section 6) |
| Frontend | `runState.test.ts` (7): replayed events are idempotent, usage adds up across pauses, draft revisions, status across pause and resume, working time excludes waiting, verbosity filtering |

## 3. Hallucination detection

**What counts as a hallucination here:**
- a number the analysis did not compute
- a citation to a fact or source that does not exist
- a statement that the facts and sources do not support

The approach has three layers.

**1. Prevention, by design.** The model never computes:
- pandas computes every number, and each result is recorded as a numbered fact (F1, F2…).
- The report writer receives only those facts, the sources' notes and quality reports, and the passages found,
  and is told to use nothing else.
- Every finding and caveat must cite what it rests on.

**2. Detection in code, on every report** (`graph/nodes/reporting.py`, `quality/grounding.py`). Every number is
extracted from the summary, findings and caveats. Each must match one of:
- a computed fact, within 1%
- a number in a fact label or in the question
- a number in a source that the same statement cites: a passage's text, or a dataset's notes and quality report

The extraction understands thousands separators, percentages and unit words in four languages ("3.7 million",
"370万"). It also applies a fact's unit ("S$5,845.3 million" for a fact of 5,845.3 in S$ million) and ignores
signs ("fell 6.8%" for −6.8). Every citation must name a fact, source or chart that exists, and every finding
and caveat must cite something.

A failing draft goes back to the writer with the exact problems, at most twice. After that, the user gets the
computed facts as a template with no generated prose. A translation that changes any number is discarded, and
the English report is shown instead.

**3. Measurement, by evaluation.**

| Measure | How | Latest result |
|---|---|---|
| Groundedness | the share of numbers in final reports that pass the check | 257 of 257 |
| What the check caught | drafts rejected by the check | 3 of 16, all four objections now fixed false alarms (section 5) |
| Faithfulness of prose | LLM-as-judge from the other provider, given exactly what the writer saw | 4.38 of 5 (was 3.62 before citations on caveats and the no-background-knowledge rule) |
| Consistency | overlap of the numbers stated by three runs of the same question | 0.77 and 0.44 |

What the code cannot check is meaning: a sentence can cite the right fact and still overstate it, or call a
fall a rise. The reviewer, and the judge in evaluation, cover meaning, and their scores are reported rather than
assumed.

**Tests that pin this behaviour down:**
- `test_ungrounded_numbers_are_sent_back_then_fall_back_to_template`
- `test_caveats_are_checked_like_findings`
- `test_a_caveat_may_quote_the_quality_report_of_the_dataset_it_cites`
- `test_translation_that_changes_a_number_falls_back_to_english`
- `test_numbers_inside_chinese_and_malay_text`
- `test_facts_in_millions_may_be_written_with_the_unit_word`
- `test_a_fall_may_be_written_without_the_minus_sign`

## 4. Coverage

Line coverage of the backend is **79%** (`uv run pytest --cov=app`, 3,536 statements):

| Area | Coverage | Note |
|---|---|---|
| Statistics, workspace, cleaning, graph routing, guardrails, options, number grounding, LLM layer | 94–100% | the logic that decides answers |
| Agent tools, reporting nodes, charts, tool loop | 78–88% | |
| API and run manager | 80–91% | |
| Data sources, registry, RAG ingest and search | 67–94% | network paths are tested with stubs; live calls are not |
| CLI | 27% | a terminal front end; the same graph is tested through the API |
| Ingest scheduler, evaluation runners | 0% | thin wrappers; the scheduler calls the tested ingest, and the runners are exercised by running the evaluations |

## 5. Evaluations

### Retrieval

**Golden set** (`retrieval_golden.jsonl`, 50 questions). Passages were sampled from the index: 8 from each
agency (MOM, MOF, PMO, MOH, URA) and 10 data.gov.sg catalogue entries, at most one per document. For each one, a
language model wrote a question that the passage answers, in its own words. The document the passage came from
is the relevant result. Built with `python -m app.evals.retrieval build`.

**Metrics**, at document level, over the top 10 distinct documents:
- **Recall@k** is the share of questions whose source document is in the top k. There is one relevant
  document per question, so this is also the hit rate.
- **MRR@10** (mean reciprocal rank) is 1 when the source document comes first, 1/2 when it comes second, and so
  on, and 0 below rank 10.

| Mode | Recall@1 | Recall@5 | Recall@10 | MRR@10 | Median latency | p95 latency |
|---|---|---|---|---|---|---|
| Keyword (BM25, SQLite FTS5) | 64% | 84% | 88% | 0.71 | 7 ms | 12 ms |
| Vector (bge-small-en-v1.5) | 46% | 78% | 88% | 0.62 | 8 ms | 11 ms |
| Hybrid (reciprocal rank fusion) | 64% | 82% | 90% | 0.74 | 16 ms | 34 ms |
| Hybrid + cross-encoder rerank (ms-marco-MiniLM-L-6-v2) | 48% | 78% | 90% | 0.64 | 536 ms | 802 ms |

**Decision (D-031):** reranking is off by default. It can be turned on with `RAG_RERANK=true`, or for a
single search with `/api/search?rerank=true`.

Index: 2,990 agency documents and 4,655 catalogue entries, 14,363 passages (14 Oct 2026).

**Reading the results.**

- Hybrid search has the best MRR. It ties keyword search at rank 1 and finds the most documents in the top 10.
- Vector search alone ranks the right document first least often, but it finds questions that keyword search
  misses. That is why the fusion helps.
- The cross-encoder reranker made ranking worse here (MRR 0.74 to 0.64), and each search took half a second
  longer. Two variants were tried on the same set:
  - Adding the document title to the reranker's input scored the same (MRR 0.64).
  - Blending the reranker's rank into the fusion, rather than letting it replace the order, scored MRR 0.71:
    close to plain hybrid, but still slower.
  The likely causes: the small model was trained on short web queries and passages, while these are long
  government texts. The generated questions also share wording with their passages, which favours BM25 over
  the reranker's semantic judgement.

**Limits.**
- Generated questions can echo their passage's wording, which favours keyword search.
- Only the source document counts as relevant. Another document that also answers the question scores as a
  miss, so the absolute numbers are conservative; the comparison between modes is the useful part.
- With 50 questions, a difference of under about 10 percentage points is within noise.

### Triage classifier

84 labelled questions held out from training (see D-024 in `DECISIONS.md` and `triage_results.json`):

| Classifier | Accuracy | Macro F1 | Recall in / off / vague | Median latency | $ per 1,000 |
|---|---|---|---|---|---|
| Local (embeddings + logistic regression) | 92% | 0.91 | 97% / 83% / 91% | 2 ms | 0 |
| Jev (TypeSafe) | 93% | 0.93 | 90% / 100% / 91% | 266 ms | 0.018 |
| Fast LLM | 100% | 1.00 | 100% / 100% / 100% | 2.2 s | 0.058 |
| **Local, unsure (< 0.6) to the LLM** (default) | 99% | 0.99 | 100% / 96% / 100% | 3 ms | 0.015 |

### Answers, end to end

**Question set** (`questions.jsonl`, 11 questions). Eight should be answered: employment, housing, COE,
life expectancy, retrenchment versus GDP, MRT stations, the internal programmes dataset, and one question asked
in Chinese. One should be declined (a 2030 forecast), one redirected as off-topic, and one should get a
clarifying question ("How is Singapore doing?"). Each question runs through the whole agent graph with the
default options (gpt-6.1-sol, low effort, all tools). The two consistency questions run three times each.

**Measures:**
- **Outcome.** Did the system answer, decline, redirect or ask, as expected?
- **Groundedness.** The share of numbers in each final report that match a computed fact, or a passage the same
  finding cites, within 1%. This uses the same rule as the report check.
- **Drafts rejected.** How often the report check sent a draft back.
- **Citation coverage.** The share of findings that cite a fact or source that exists.
- **LLM-as-judge.** Each report is scored 1–5 by claude-sonnet-5-5, the other provider, so no model grades its
  own work. The criteria are relevance, completeness, faithfulness, clarity and caveats. The judge sees only
  what the report writer saw: every cited fact, the dataset caveats and the cited passages.
- **Consistency.** The average overlap (Jaccard) of the numbers stated by repeated runs of the same question.

**Results** (14 Oct 2026, final run; `answers_results.json`):

| Question | Expected | Outcome | Time | LLM calls | Cost | Numbers grounded | Statements cited | Judge: rel / comp / faith / clarity / caveats |
|---|---|---|---|---|---|---|---|---|
| tech-employment | answered | answered | 104 s | 14 | $0.13 | 32/32 | 100% | 5 / 5 / 5 / 5 / 4 |
| hdb-towns | answered | answered | 127 s | 15 | $0.23 | 46/46 | 100% | 4 / 4 / 4 / 5 / 5 |
| coe | answered | partial | 154 s | 16 | $0.17 | 37/37 | 100% | 5 / 4 / 5 / 5 / 5 |
| life-expectancy | answered | answered | 88 s | 14 | $0.11 | 38/38 | 100% | 5 / 4 / 4 / 5 / 5 |
| retrenchment-gdp | answered | answered | 138 s | 18 | $0.12 | 21/21 | 100% | 5 / 4 / 4 / 4 / 5 |
| mrt-stations | answered or partial | answered | 101 s | 15 | $0.10 | 2/2 | 100% | 4 / 3 / 4 / 5 / 4 |
| internal-programmes | answered | answered | 84 s | 13 | $0.08 | 32/32 | 100% | 5 / 5 / 5 / 4 / 5 |
| chinese (in Chinese) | answered | answered | 125 s | 18 | $0.38 | 49/49 | 100% | 5 / 4 / 4 / 4 / 5 |
| forecast | declined | declined | 4 s | 1 | $0.00 | | | |
| off-topic | off_topic | off_topic | 0 s | 0 | $0.00 | | | |
| vague | clarification | clarification | 2 s | 1 | $0.00 | | | |

"Statements cited" counts findings and caveats.

**Reducing hallucination (D-031).** The first evaluation showed that numbers were always grounded, but sentences
were not: faithfulness was the lowest judge score. Two changes were made:
1. **Tighter report prompt.** Every statement must be supported by the facts, source notes or passages, with no
   background knowledge.
2. **Caveats checked like findings.** Caveats now carry citations, and the report check covers them as it does
   findings.

Before and after:

| | Before | After |
|---|---|---|
| Outcomes as expected | 11 of 11 | 10 of 11 |
| Numbers grounded | 227 of 227 | 257 of 257 |
| Judge: faithfulness | 3.62 | **4.38** |
| Judge: caveats | 3.88 | **4.75** |
| Judge: relevance / completeness / clarity | 4.88 / 4.12 / 4.62 | 4.75 / 4.12 / 4.62 |
| Drafts rejected by the report check | 1 of 13 | 3 of 16 |
| Consistency (tech-employment / hdb-towns) | 0.79 / 0.80 | 0.77 / 0.44 |
| Median time for an answered question | 104 s | 115 s |
| Cost of a full run | $1.94 | $1.91 |

**What the intermediate runs exposed** (three runs between before and after):
- **Declines.** Some questions were declined when they could have been answered. The extraction agent reported
  "no annual figures", because loading a dataset returns its profile, not its values, and the coordinator
  believed it. The coordinator now also sees, recorded by the system, which datasets are loaded and how many
  facts exist. Both prompts now say that extraction never returns figures.
- **False alarms in the report check.** The stricter rules made the check fire more often, which exposed four
  problems with the check itself. All four are fixed and covered by tests:
  1. A caveat quoting a dataset's quality report ("5,546 values from 12,012 source rows") was rejected. A claim
     may now use numbers from the notes of the dataset it cites.
  2. "S$5,845.3 million" was rejected because the fact is stored in millions. Units are now applied.
  3. "Fell 6.8%" was rejected because the fact is −6.8. Signs are now ignored, and direction is left to the
     reviewer.
  4. Citing a chart ID was treated as an unknown citation.

  Every objection in the final run was one of the last two, so none was a real error by the writer. The three
  rejected drafts were all rewritten and passed.
- **Remaining variation.** COE ended "partial" here, after the reviewer's objection, but answered in the
  previous two runs. The HDB question is answered every time, but runs pick different flat types and periods to
  rank, so their numbers overlap less (0.44).

**Limits.**
- Eleven questions show the system's behaviour, not its accuracy across all questions.
- The judge is a single model run, not calibrated against human ratings; same-provider bias is avoided, but
  model bias is not.
- Results vary between runs: consistency is about 0.8. One of three COE runs during development fell back to the
  template report; the runner now records each run's errors, so the next occurrence will show why.

Re-run everything: `uv run python -m app.evals.answers` (about $2). Re-run some questions and merge them into
the saved results: `--only id1,id2`. Score the saved reports again with a different rubric: `--rejudge`.

## 6. Performance and load

**HTTP load** (`scripts/load_test.py`). 20 concurrent clients sent a mix of requests for 30 seconds to the API on
a laptop (14 Oct 2026, `load_results.json`):

| Endpoint | Requests | Errors | p50 | p95 | Max |
|---|---|---|---|---|---|
| health | 1,460 | 0 | 2 ms | 22 ms | 766 ms |
| options | 1,464 | 0 | 2 ms | 29 ms | 257 ms |
| datasets | 1,467 | 0 | 4 ms | 33 ms | 566 ms |
| conversations | 1,470 | 0 | 6 ms | 36 ms | 385 ms |
| search (keyword) | 1,473 | 0 | 144 ms | 230 ms | 478 ms |
| search (hybrid) | 1,469 | 0 | 225 ms | 347 ms | 574 ms |

That is 8,803 requests, 0 errors, 292 requests per second.

The first run of this test found a real bug. The document index shared one SQLite connection across the API's
threads, and concurrent searches failed or mixed up rows: 0.7% errors and 169 requests per second. Index access
is now serialised by a lock. A unit test with 16 threads fails without the fix.
Searches now queue behind each other under load: a keyword search takes 144 ms at the median here, against 7 ms
alone.

**Concurrent runs** (`tests/integration/test_load.py`, in every CI run). 20 questions are posted at the same
moment through the API, the run manager, the agent graph and SQLite, with scripted models. All 20 complete in
about 0.6 s, every run's events are numbered without gaps, and replay still works. With real models, three
questions run in parallel in the answer evaluation.

**Cost and time per question** (answer evaluation):
- 1–3 minutes per answered question (median 115 s)
- $0.05–0.40 each
- 11–18 LLM calls

Most of the time is model latency. The live view shows elapsed time, tokens and cost while a run works.

## 7. Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

1. **Backend:** `ruff` lint, then all 187 tests with coverage.
2. **Frontend:** type check, unit tests and a production build.
3. **Images:** build both images, start the API and web UI with Docker Compose, then smoke-test:
   - API health
   - all 19 datasets listed
   - the web page served
4. **Publishing (on a version tag):** push both images to the GitHub Container Registry.

The evaluations call paid APIs, so they run on demand, not in CI. Their results are committed.

## 8. Limits

- **Small sets.** The evaluation sets show behaviour, not population accuracy: 11 questions, 50 retrieval
  questions, 84 triage questions. Differences of a few points are within noise.
- **Not human-validated.** Labels and generated questions were drafted with AI help. The judge is one model
  run, not calibrated against human ratings.
- **Variation between runs.** Repeated runs agree on most numbers for a narrow question (0.77) and fewer for an
  open one (0.44), and an outcome can change between runs (COE was partial once, answered twice).
