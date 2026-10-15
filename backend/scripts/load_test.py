"""HTTP load test against a running API: many concurrent clients on the read and search endpoints.

    uv run python scripts/load_test.py --url http://localhost:8000 --clients 20 --seconds 30

Each client sends a fixed mix of requests, one after another, for the given time. The result is, per endpoint,
the number of requests and errors and the median, 95th percentile and maximum latency, plus the overall
throughput. Results are saved to data/eval/load_results.json.

Asking questions is left out on purpose: each one costs LLM calls and takes about two minutes, so the load on
the system is set by the model providers, not by this server. Concurrent runs are tested separately with
scripted models (tests/integration/test_load.py).
"""
import argparse
import asyncio
import datetime as dt
import json
import time

import httpx

from app.evals.metrics import percentile
from app.evals.retrieval import EVAL_DIR

MIX = [
    ("health", "/api/health"),
    ("options", "/api/options"),
    ("datasets", "/api/datasets"),
    ("conversations", "/api/threads"),
    ("search (keyword)", "/api/search?mode=keyword&q=housing+grants+for+first-time+buyers"),
    ("search (hybrid)", "/api/search?mode=hybrid&q=support+for+mid-career+workers+changing+jobs"),
]


async def client(http: httpx.AsyncClient, offset: int, until: float, results: dict) -> None:
    i = offset  # clients start at different points in the mix
    while time.perf_counter() < until:
        name, path = MIX[i % len(MIX)]
        i += 1
        started = time.perf_counter()
        try:
            ok = (await http.get(path)).status_code == 200
        except httpx.HTTPError:
            ok = False
        results.setdefault(name, []).append(((time.perf_counter() - started) * 1000, ok))


async def run(url: str, clients: int, seconds: float) -> dict:
    async with httpx.AsyncClient(base_url=url, timeout=60) as http:
        (await http.get("/api/health")).raise_for_status()  # fail fast if the API is not running
        results: dict = {}
        started = time.perf_counter()
        await asyncio.gather(*(client(http, i, started + seconds, results) for i in range(clients)))
        elapsed = time.perf_counter() - started
    endpoints = {}
    for name, _ in MIX:
        samples = results.get(name, [])
        latencies = [ms for ms, _ in samples]
        endpoints[name] = {"requests": len(samples), "errors": sum(1 for _, ok in samples if not ok),
                           "p50_ms": round(percentile(latencies, 50), 1), "p95_ms": round(percentile(latencies, 95), 1),
                           "max_ms": round(max(latencies, default=0), 1)}
    total = sum(e["requests"] for e in endpoints.values())
    return {"date": dt.date.today().isoformat(), "clients": clients, "seconds": round(elapsed, 1),
            "requests": total, "errors": sum(e["errors"] for e in endpoints.values()),
            "throughput_rps": round(total / elapsed, 1), "endpoints": endpoints}


def table(result: dict) -> str:
    lines = ["| Endpoint | Requests | Errors | p50 | p95 | Max |", "|---|---|---|---|---|---|"]
    for name, e in result["endpoints"].items():
        lines.append(f"| {name} | {e['requests']} | {e['errors']} | {e['p50_ms']:.0f} ms | {e['p95_ms']:.0f} ms | "
                     f"{e['max_ms']:.0f} ms |")
    lines.append(f"\n{result['clients']} clients for {result['seconds']} s: {result['requests']} requests, "
                 f"{result['errors']} errors, {result['throughput_rps']} requests per second")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP load test against a running API")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    outcome = asyncio.run(run(args.url, args.clients, args.seconds))
    (EVAL_DIR / "load_results.json").write_text(json.dumps(outcome, indent=1) + "\n")
    print(table(outcome))
