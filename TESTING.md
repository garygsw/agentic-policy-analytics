# Testing

How the system is tested, how hallucination is detected and measured, and the results. Everything here can be
re-run from `backend/` (and `frontend/` for the UI tests). Results are saved in `backend/data/eval/`.

| Level | What it checks | Count | Command | Cost |
|---|---|---|---|---|
| Unit tests | agents, tools, statistics, data cleaning, maps, guardrails, LLM layer, RAG, report check, downloads | 207 | `uv run pytest tests/unit` | free (fake models) |
| Integration tests | the whole graph and API: background runs, pauses, streaming, replay, concurrency, restarts, downloads | 25 | `uv run pytest tests/integration` | free (scripted models) |
| Frontend tests | the event reducer behind the live view, the history list, sidebar paging and width | 12 | `npm test` (in `frontend/`) | free |
| Live provider tests | real calls to both providers, structured output, fallback | 3 | `uv run pytest -m live` | cents |
| Retrieval evaluation | recall@k and MRR for each search mode | 50 questions | `uv run python -m app.evals.retrieval run` | free |
| Triage evaluation | accuracy, F1, latency and cost of each classifier | 84 questions | `uv run python scripts/triage_trial.py` | cents |
| Answer evaluation | outcomes, groundedness, citations, LLM-as-judge, consistency | 12 questions | `uv run python -m app.evals.answers` | about $4 |
| Load test | latency and throughput under concurrent clients | 20 clients | `uv run python scripts/load_test.py` | free |

`uv run pytest` runs the unit and integration tests together (232), with no network access and no API keys. CI
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
| Agents and tools | `test_analysis.py` (33): every analytics tool, summing rules, error hints, the tool loop and its step limit, extraction then analysis with shared source IDs, retries keeping earlier results, `rank_changes`, every chart kind: line, bar, scatter (exactly the points its correlation uses), pie (refused where the parts do not add up; small slices combined), box and dot (quartiles by group, pooled dimensions named), heatmap (by period or by a second dimension), grouped and stacked bars (stacked refused where the parts do not add up), maps (towns filled by value, what was left out named; NEA regions as points; no map for labels that are not places). `test_geo.py` (4): every committed boundary layer, towns to planning areas, each kind of region, electoral boundaries by year |
| API endpoints | `test_api.py` (24): health, readiness and options, background runs, numbered events, WebSocket replay, reports and their security policy, personal-data rejection, clarification pause and resume (with answer choices), cancelling, invalid input, search and datasets, renaming, pinning, soft-deleting and restoring conversations, searching them, adding new columns to an older database, a run cut off by a restart continuing (or ending after a second cut), Stop on a run whose worker is gone or still working, a dataset's profile, rejecting a plan then approving it later, a new question after a rejected plan |
| Multi-agent workflows | `test_graph.py` (29): the full graph with scripted models (coordinator, extraction, analytics, report writer, number check, reviewer, translation) across every route, including a conversation surviving a restart and a translation that changes a number, a revised plan not counting the rejected one as done, filler cut from a plan |
| Hallucination detection | `test_grounding.py` (7) and the report-check tests in `test_graph.py`; measured by the answer evaluation (section 3) |
| Accuracy and consistency | the answer evaluation: expected outcomes, judge scores, overlap between repeated runs |
| Data quality validation | `test_data.py` (37): period normalisation, number parsing, placeholders, duplicates, total rows, case-merged labels, personal columns dropped, rail-station names matched across layers, the internal workbook's planted problems, every committed snapshot loading, cleaned tables stored in and read back from the database, a town published under two names, PSI summarised from both timestamp formats, election results without walkovers or candidate names |
| Error handling | `test_llm_layer.py`: provider failure and fallback, including structured output. `test_data.py`: retries, then stale cache, then snapshot. `test_graph.py`: model failure leads to the template report; an unanswerable question is declined; a vague one gets a clarifying question |
| Guardrails | `test_pii.py` (19): NRIC/FIN, emails, phones, unit numbers, bank accounts, cards (Luhn); names and statistics allowed. `test_language.py` (9). `test_triage.py` (8) |
| Retrieval | `test_rag.py` (24): chunking, crawling rules, incremental re-crawls, each search mode, fusion, filters, per-document cap, web result de-duplication, concurrent searches |
| Performance and load | `test_load.py`: 20 concurrent runs through the API with no lost events. `scripts/load_test.py`: HTTP load (section 6) |
| Frontend | `runState.test.ts` (8): replayed events are idempotent, a rejected plan kept to reopen, usage adds up across pauses, draft revisions, status across pause and resume, working time excludes waiting, verbosity filtering. `threads.test.ts` (1): pinned conversations listed apart. `layout.test.ts` (3): sidebar width limits, paging, the list request |

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
| Groundedness | the share of numbers in final reports that pass the check | 460 of 460 |
| What the checks caught | drafts sent back | number check: 0 of 21; claim checker: about 7 rewrites (section 5) |
| Faithfulness of prose | LLM-as-judge from the other provider, given what the writer saw | 4.00 of 5 (3.62 before citations on caveats; see section 5 for the judge's blind spots) |
| Consistency | overlap of the numbers stated by three runs of the same question | 0.66 and 0.38 |

What the code cannot check is meaning: a sentence can cite the right fact and still overstate it, rank it
wrongly, or call a fall a rise. That is the claim checker's job (D-035). The reviewer, a model from the other
provider, checks each statement against what it cites and sends unsupported ones back to the writer. A
statement still unsupported after two fixes is shown flagged ⚠, never dropped. Its effect is measured by the
judge (section 5); the results so far are mixed.

**Tests that pin this behaviour down:**
- `test_ungrounded_numbers_are_sent_back_then_fall_back_to_template`
- `test_caveats_are_checked_like_findings`
- `test_a_caveat_may_quote_the_quality_report_of_the_dataset_it_cites`
- `test_translation_that_changes_a_number_falls_back_to_english`
- `test_numbers_inside_chinese_and_malay_text`
- `test_facts_in_millions_may_be_written_with_the_unit_word`
- `test_a_fall_may_be_written_without_the_minus_sign`

## 4. Coverage

Line coverage of the backend is **83%** (`uv run pytest --cov=app`, 4,079 statements). Without the
command-line programs (the evaluation runners, the terminal client, the ingest scheduler and the admin
commands), it is **92.5%** (3,580 statements). CI saves the full report with every run: `coverage.xml` and an
HTML report, under the run's artifacts (section 7).

| Area | Coverage | Note |
|---|---|---|
| Statistics, workspace, cleaning, graph routing, guardrails, options, number grounding, LLM layer | 93–100% | the logic that decides answers |
| Agent tools, reporting nodes, charts, the dataset store and its tables | 91–99% | |
| API and run manager | 93–94% | |
| Data sources, curated cleaners, RAG ingest and search | 67–94% | network paths are tested with stubs; live calls are not. The cleaners of some curated datasets run only when snapshots are rebuilt from live data |
| CLI, admin commands | 34–56% | terminal front ends; the same graph and functions are tested through the API |
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

**Question set** (`questions.jsonl`, 12 questions):
- **Nine should be answered:** employment, housing, COE, life expectancy, retrenchment versus GDP, MRT
  stations, the internal programmes dataset, a question asked in Chinese, and an announced future policy
  ("petrol cars phased out by 2030?").
- **A 2030 price forecast** should be declined, or answered with clearly third-party projections; published
  sources are searched first.
- **One is off topic** and should be redirected.
- **One is vague** and should get a clarifying question ("How is Singapore doing?").

Each question runs through the whole agent graph with the default options: Claude Sonnet 5.5, low effort, all
tools. The two consistency questions run three times each.

**Measures:**
- **Outcome.** Did the system answer, decline, redirect or ask, as expected?
- **Groundedness.** The share of numbers in each final report, including caveats, that match a computed fact or
  a source the same statement cites, within 1%. This uses the same rule as the report check.
- **Drafts rejected.** How often the report check sent a draft back.
- **Citation coverage.** The share of findings and caveats that cite a fact or source that exists.
- **LLM-as-judge.** Each report is scored 1–5 by the other provider's model (GPT 6.1 Sol for Claude Sonnet 5.5
  reports), so no model grades its own work. The criteria are relevance, completeness, faithfulness, clarity
  and caveats. The judge sees exactly what the report writer saw: every fact with its method, every source with
  its metadata, and the cited passages.
- **Consistency.** The average overlap (Jaccard) of the numbers stated by repeated runs of the same question.

**Results** (15 Oct 2026, second run: claim checker on, and the judge sees charts and the analysis summary;
`answers_results.json`):

| Question | Expected | Outcome | Time | LLM calls | Cost | Numbers grounded | Statements cited | Judge: rel / comp / faith / clarity / caveats |
|---|---|---|---|---|---|---|---|---|
| tech-employment | answered | answered | 59 s | 10 | $0.15 | 70/70 | 100% | 5 / 5 / 5 / 5 / 5 |
| hdb-towns | answered | answered | 129 s | 18 | $0.59 | 53/53 | 100% | 5 / 4 / 5 / 4 / 4 |
| coe | answered | answered | 113 s | 18 | $0.20 | 43/43 | 100% | 5 / 4 / 5 / 4 / 4 |
| life-expectancy | answered | answered | 56 s | 11 | $0.13 | 53/53 | 100% | 5 / 4 / 5 / 5 / 4 |
| retrenchment-gdp | answered | answered | 80 s | 15 | $0.19 | 35/35 | 100% | 5 / 4 / 5 / 4 / 4 |
| mrt-stations | answered or partial | answered | 114 s | 18 | $0.30 | 15/15 | 100% | 5 / 5 / 5 / 4 / 4 |
| internal-programmes | answered | answered | 66 s | 14 | $0.20 | 81/81 | 100% | 5 / 5 / 5 / 4 / 4 |
| chinese (in Chinese) | answered | answered | 92 s | 15 | $0.21 | 64/64 | 100% | 5 / 5 / 5 / 5 / 5 |
| future-policy | answered | answered | 121 s | 14 | $0.35 | 57/57 | 100% | 4 / 4 / **1** / 3 / 3 |
| forecast | declined or answered | declined (after a search) | 18 s | 4 | $0.05 | | | |
| off-topic | off_topic | off_topic | 0 s | 0 | $0.00 | | | |
| vague | clarification | clarification | 1 s | 1 | $0.00 | | | |

"Statements cited" counts findings and caveats.

**Across the configurations** (each a full run of the set):

| | GPT 6.1 Sol, first run | GPT 6.1 Sol, after D-031 | Claude Sonnet 5.5, after D-034 | + claim checker (D-035) | + judge sees charts and analysis |
|---|---|---|---|---|---|
| Outcomes as expected | 11 of 11 | 10 of 11 | **12 of 12** | **12 of 12** | **12 of 12** |
| Numbers grounded | 227 of 227 | 257 of 257 | 494 of 494 | 460 of 460 | 471 of 471 |
| Drafts rejected by the number check | 1 of 13 | 3 of 16 | 1 of 14 | 0 of 21 | 0 of 24 |
| Judge: faithfulness | 3.62 | 4.38 | 4.33 | 4.00 | **4.56** |
| Judge: relevance / completeness / clarity / caveats | 4.88 / 4.12 / 4.62 / 3.88 | 4.75 / 4.12 / 4.62 / 4.75 | 4.78 / 4.67 / 4.56 / 4.78 | 4.89 / 4.56 / 4.56 / 4.56 | 4.89 / 4.44 / 4.22 / 4.11 |
| Consistency (tech-employment / hdb-towns) | 0.79 / 0.80 | 0.77 / 0.44 | 0.80 / 0.60 | 0.66 / 0.38 | 0.72 / 0.54 |
| Median time for an answered question | 104 s | 115 s | **51 s** | 59 s | 92 s |
| Cost of a full run | $1.94 | $1.91 | $2.60 (12 questions) | $3.77 (12 questions) | $3.58 (12 questions) |

The judge was Claude Sonnet 5.5 for the first two columns and GPT 6.1 Sol for the last three, so the
faithfulness scores are close but not strictly comparable. In the last run the dataset cache started empty (it had
just moved into the database, D-042), so the first runs fetched every dataset live; repeats of the same question
took 43–65 s.

**What each round changed:**
- **Prose was not grounded (D-031).** The first run showed every number grounded, but faithfulness was the
  lowest judge score: caveats made claims nothing supported. Two changes followed:
  - Caveats now carry citations and are checked like findings.
  - The writer may use no background knowledge.

  Faithfulness rose from 3.62 to 4.38.
- **False alarms in the check.** The stricter check then exposed five false alarms in the check itself, all
  fixed and covered by tests:
  1. A caveat quoting a dataset's quality report.
  2. "S$5,845.3 million" for a fact stored in millions.
  3. "Fell 6.8%" for a fact of −6.8.
  4. A citation of a chart ID.
  5. A source's retrieval date ("2026-10-14", read as an unmatched 14). Found in hands-on testing; D-034.
- **Declines.** Some questions were declined when they could have been answered:
  - The extraction agent reported "no annual figures", because loading a dataset returns its profile, not its
    values. The coordinator now also sees what the system has recorded as loaded and computed.
  - An announced future policy was declined without any search. The coordinator now cannot decline before a
    search (D-034).
- **The judge's own inputs.** Twice, the judge flagged correct statements as unsupported because it saw less
  than the writer did:
  - The first time, facts were trimmed and dataset caveats left out.
  - The second time, source metadata such as retrieval dates and publishers, and each fact's method, were left
    out.

  The judge now receives exactly the writer's material. Without this fix, the third run scored faithfulness
  1.89; with it, 4.33.

- **The claim checker (D-035), measured.** The reviewer, a model from the other provider, now checks each
  statement against what it cites and sends unsupported ones back.
  - **What it did:** it rewrote statements in most reports (24 drafts for 9 reports, against 14 without it),
    and the number check then had nothing to reject.
  - **What it cost:** about 38–45% more per run ($3.58 against $2.60).
  - **What the judge saw:** the first run with the checker scored faithfulness 4.00, but both of its 1-scores
    were about claims the judge could not check (what chart C1 shows, what the analysis did), because the judge
    was not given the charts or the analysis summary. With them, the second run scored **4.56**, against 4.33
    without the checker: eight of nine reports scored 5. The difference is small for nine reports, so the
    evidence is that the checker does not hurt and probably helps.
- **Source attribution, the error that remains.** The one low score (future-policy, faithfulness 1) was about
  who said something, not about a number. The report called government web pages "third-party sources",
  because the writer's prompt described all web results as third-party. The prompt now says a web result is
  official when its URL is a government site. A re-run of that question no longer mislabelled the pages, but
  scored 1 again for a related error: its summary said "third-party and official pages" describe the 2030
  target, when only a third-party page in its material did. The number check cannot see attribution, and the
  claim checker missed it twice; it is the main error class left.

**What the checks caught in an earlier run (D-034 column):**
- **The number check.** In the life-expectancy report, the writer computed a gap of "4.44 years" between men and
  women itself. The check rejected it, since 4.44 is not a computed fact, and the revised draft passed.
- **The judge.** The HDB report claimed that Bukit Batok "alone" ranks in the top six for three flat types,
  when Woodlands does too. The number check cannot see ordering or exclusivity claims like this, so the judge
  rated faithfulness 1. This is the kind of error that remains: wrong words around right numbers.

**Limits.**
- **Behaviour, not accuracy.** Twelve questions show the system's behaviour, not its accuracy across all
  questions.
- **One uncalibrated judge.** The judge is a single model run, not calibrated against human ratings.
  Same-provider bias is avoided, but model bias is not.
- **Variation between runs.** Results vary: consistency is 0.6–0.8. An open question such as the HDB ranking
  picks different flat types and periods on each run.

Re-run everything: `uv run python -m app.evals.answers` (about $3.80). Re-run some questions and merge them into
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
- 35–70 s per answered question (median 51 s, default model Claude Sonnet 5.5)
- $0.05–0.30 each
- 10–19 LLM calls

Most of the time is model latency. The live view shows elapsed time, tokens and cost while a run works.

## 7. Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

1. **Backend:** `ruff` lint, then all 232 tests with coverage. The coverage report (`coverage.xml` and HTML) is
   kept with the run for 30 days.
2. **Frontend:** type check, unit tests and a production build.
3. **Secrets:** gitleaks scans every commit in the history, not just the latest, since a key removed later is
   still published. (A local pre-commit hook also blocks keys before they are committed.)
4. **Images:** build both images, start the API and web UI with Docker Compose, then smoke-test:
   - API health
   - all 21 datasets listed
   - the web page served
5. **Publishing (on a version tag):** push both images to the GitHub Container Registry (README, "Deployment").

The evaluations call paid APIs, so they run on demand, not in CI. Their results are committed.

## 8. Limits

- **Small sets.** The evaluation sets show behaviour, not population accuracy: 12 answer questions, 50 retrieval
  questions, 84 triage questions. Differences of a few points are within noise.
- **Not human-validated.** Labels and generated questions were drafted with AI help. The judge is one model
  run, not calibrated against human ratings.
- **Variation between runs.** Repeated runs agree on most numbers for a narrow question (0.66–0.80 across the
  runs) and fewer for an open one (0.38–0.60). Outcomes have been stable in the last three runs (12 of 12 each);
  in an earlier run, one question was answered only partly once.
- **Attribution is not checked in code.** Numbers are checked against the facts, and statements against what
  they cite, but who said something (an official source or a third party) is checked only by the reviewer, which
  missed it in one report (section 5).
