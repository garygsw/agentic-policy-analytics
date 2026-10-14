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
