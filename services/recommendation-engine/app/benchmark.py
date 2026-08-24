"""Measure local database-backed recommendation latency and memory."""

import argparse
import json
import os
import resource
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter

from fastapi.testclient import TestClient

DEFAULT_QUERIES = (
    "Camden pub with live music",
    "Camden pub with regular events",
    "Westminster pub with food and outdoor seating",
    "step-free pub in Westminster",
    "pub with no sports",
)


def percentile95(values: list[float]) -> float:
    """Return a stable p95 for small local benchmark samples."""
    return quantiles(values, n=20, method="inclusive")[18] if len(values) > 1 else values[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")

    os.environ["APP_DATABASE_PATH"] = str(args.database)
    os.environ.setdefault("RAG_MODEL_CACHE_PATH", "data/local/models")
    from app.main import app

    timings: list[float] = []
    started = perf_counter()
    with TestClient(app) as client:
        startupMilliseconds = (perf_counter() - started) * 1000
        for _ in range(args.iterations):
            for query in DEFAULT_QUERIES:
                requestStarted = perf_counter()
                response = client.post(
                    "/recommendations", json={"query": query, "limit": 5}
                )
                response.raise_for_status()
                timings.append((perf_counter() - requestStarted) * 1000)
        readiness = client.get("/ready").json()

    report = {
        "database": str(args.database),
        "queries": len(DEFAULT_QUERIES) * args.iterations,
        "startupMilliseconds": round(startupMilliseconds, 2),
        "medianLatencyMilliseconds": round(median(timings), 2),
        "p95LatencyMilliseconds": round(percentile95(timings), 2),
        "maximumLatencyMilliseconds": round(max(timings), 2),
        # macOS reports ru_maxrss in bytes; Linux reports KiB.
        "peakResidentMemoryMb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2),
        "readiness": readiness,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
