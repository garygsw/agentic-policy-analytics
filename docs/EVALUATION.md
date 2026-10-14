# Evaluation

How the system is measured, what the numbers are, and what they do not show. Every evaluation can be re-run
from `backend/`; results are saved under `backend/data/eval/` so changes can be compared over time.

| What | Metric | Command | Cost |
|---|---|---|---|
| Unit and integration tests | pass/fail, line coverage | `uv run pytest --cov=app` | free (fake models) |
| Retrieval | recall@k, MRR@10, latency | `uv run python -m app.evals.retrieval run` | free (local models) |
| Triage classifier | accuracy, macro F1, recall per class, latency, cost | `uv run python scripts/triage_trial.py` | cents |
| Answers, end to end | outcome, groundedness, citations, LLM-as-judge, consistency, time, cost | `uv run python -m app.evals.answers` | about $2 |

## Retrieval

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

## Triage classifier

84 labelled questions held out from training (see D-024 in `DECISIONS.md` and `triage_results.json`):

| Classifier | Accuracy | Macro F1 | Recall in / off / vague | Median latency | $ per 1,000 |
|---|---|---|---|---|---|
| Local (embeddings + logistic regression) | 92% | 0.91 | 97% / 83% / 91% | 2 ms | 0 |
| Jev (TypeSafe) | 93% | 0.93 | 90% / 100% / 91% | 266 ms | 0.018 |
| Fast LLM | 100% | 1.00 | 100% / 100% / 100% | 2.2 s | 0.058 |
| **Local, unsure (< 0.6) to the LLM** (default) | 99% | 0.99 | 100% / 96% / 100% | 3 ms | 0.015 |

## Answers, end to end

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

**Results** (14 Oct 2026; `answers_results.json`):

| Question | Expected | Outcome | Time | LLM calls | Cost | Numbers grounded | Findings cited | Judge: rel / comp / faith / clarity / caveats |
|---|---|---|---|---|---|---|---|---|
| tech-employment | answered | answered | 91 s | 13 | $0.16 | 42/42 | 100% | 5 / 5 / 4 / 5 / 4 |
| hdb-towns | answered | answered | 106 s | 14 | $0.34 | 37/37 | 100% | 5 / 4 / 3 / 5 / 4 |
| coe | answered | answered | 136 s | 18 | $0.16 | 33/33 | 100% | 5 / 4 / 3 / 4 / 4 |
| life-expectancy | answered | answered | 102 s | 17 | $0.11 | 30/30 | 100% | 5 / 4 / 3 / 5 / 3 |
| retrenchment-gdp | answered | answered | 123 s | 16 | $0.11 | 16/16 | 100% | 5 / 4 / 5 / 4 / 5 |
| mrt-stations | answered or partial | partial | 197 s | 25 | $0.24 | 5/5 | 100% | 4 / 3 / 2 / 4 / 3 |
| internal-programmes | answered | answered | 66 s | 12 | $0.05 | 32/32 | 100% | 5 / 5 / 5 / 5 / 4 |
| chinese (in Chinese) | answered | answered | 75 s | 13 | $0.15 | 32/32 | 100% | 5 / 4 / 4 / 5 / 4 |
| forecast | declined | declined | 6 s | 1 | $0.00 | | | |
| off-topic | off_topic | off_topic | 0 s | 0 | $0.00 | | | |
| vague | clarification | clarification | 2 s | 1 | $0.00 | | | |

| Summary | |
|---|---|
| Outcomes as expected | 11 of 11 |
| Numbers grounded | 227 of 227 (100%) |
| Drafts rejected by the report check | 1 of 13 |
| Findings with a valid citation | 100% |
| Judge, mean of 8 reports | relevance 4.88, completeness 4.12, faithfulness 3.62, clarity 4.62, caveats 3.88 |
| Consistency (same numbers across 3 runs) | tech-employment 0.79, hdb-towns 0.80; same outcome every time |
| Median time for an answered question | 104 s |
| Cost of the whole evaluation | $1.94 |

**What the evaluation found, and what was changed because of it:**
- **The judge's own inputs.** The first run gave the judge only 150 facts and no dataset caveats, so correct
  citations looked invented. Fixed before the results above.
- **Datasets the coordinator didn't know about.** The coordinator asked a clarifying question about "internal
  tech talent programmes" because it did not know that dataset existed. It now sees the curated dataset titles.
- **Questions the data doesn't fit.** Two questions were reworded or given wider expectations, because the data
  could not answer them as first written:
  - The internal dataset records participants, not completions. The agents declined rather than relabel one
    measure as the other.
  - The stations dataset has no line information, so "partial" is an honest outcome for "on which lines".
- **Qualitative claims are the weak point.** Faithfulness is the lowest judge score (3.6 of 5). Numbers are
  checked in code, but sentences are not. The judge flagged claims such as "2025 figures are preliminary" and
  "these are June snapshots", which no listed fact or passage supports. See D-030 in `DECISIONS.md`.

**Limits.**
- Eleven questions show the system's behaviour, not its accuracy across all questions.
- The judge is a single model run, not calibrated against human ratings; same-provider bias is avoided, but
  model bias is not.
- Results vary between runs: consistency is about 0.8. One of three COE runs during development fell back to the
  template report; the runner now records each run's errors, so the next occurrence will show why.

Re-run everything: `uv run python -m app.evals.answers` (about $2). Re-run some questions and merge them into
the saved results: `--only id1,id2`. Score the saved reports again with a different rubric: `--rejudge`.
