"""Measure dashboard load, filter response, concurrency and memory.

Runs the API in-process (ASGI test client, no network) against the synthetic
sample warehouse. Numbers characterise the engine and API overhead on a small
dataset (~12k rows); they are not production-scale capacity figures.

    python scripts/benchmark.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--requests", type=int, default=25)
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="intelliq-bench-")
    os.environ.update({"INTELLIQ_DATA_DIR": tmp, "INTELLIQ_DEV": "1", "INTELLIQ_QUERY_RATE": "100000", "INTELLIQ_LOGIN_RATE": "1000"})
    from fastapi.testclient import TestClient

    from intelliq.api.app import create_app
    from intelliq.platform.bootstrap import ensure_admin, seed_sample

    app = create_app()
    ctx = app.state.ctx
    admin = ensure_admin(ctx.store, "bench@example.com", "bench-password-1")
    started = time.perf_counter()
    did = seed_sample(ctx.store, ctx.settings.warehouse_dir, admin["id"])
    seed_s = time.perf_counter() - started

    def client() -> TestClient:
        c = TestClient(app)
        c.post("/api/auth/login", json={"email": "bench@example.com", "password": "bench-password-1"})
        c.headers["X-Intelliq-CSRF"] = c.get("/api/auth/me").json()["csrf_token"]
        return c

    c = client()
    spec = c.get(f"/api/dashboards/{did}").json()["spec"]
    worksheets = [w["id"] for w in spec["worksheets"]]

    def load(state: dict) -> tuple[float, list[float], list[float]]:
        per, server = [], []
        t0 = time.perf_counter()
        for wid in worksheets:
            t = time.perf_counter()
            r = c.post(f"/api/dashboards/{did}/worksheets/{wid}/data", json={"state": state})
            r.raise_for_status()
            per.append((time.perf_counter() - t) * 1000)
            server.append(r.json()["duration_ms"])
        return (time.perf_counter() - t0) * 1000, per, server

    ctx.executor.cache.clear()
    cold_total, cold_per, cold_server = load({})
    warm_total, warm_per, _ = load({})
    ctx.executor.cache.clear()
    filt_total, filt_per, _ = load({"filters": {"f_segment": {"values": ["Corporate"]}}})
    ctx.executor.cache.clear()
    xf_total, _, _ = load({"selections": [{"action_id": "region_filter", "tuples": [{"region": "West"}]}]})

    # Concurrency: uncached requests (cache disabled) from N threads.
    original_ttl = ctx.executor.cache.put
    ctx.executor.cache.put = lambda *a, **k: None  # type: ignore[method-assign]
    latencies: list[float] = []
    lock = threading.Lock()
    errors = 0

    def worker(i: int):
        nonlocal errors
        cl = client()
        for j in range(args.requests):
            wid = worksheets[(i + j) % len(worksheets)]
            t = time.perf_counter()
            r = cl.post(f"/api/dashboards/{did}/worksheets/{wid}/data", json={"state": {}})
            ms = (time.perf_counter() - t) * 1000
            with lock:
                latencies.append(ms)
                errors += r.status_code != 200

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(args.threads)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    ctx.executor.cache.put = original_ttl  # type: ignore[method-assign]

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    results = {
        "dataset_rows": 12_000,
        "worksheets": len(worksheets),
        "seed_seconds": round(seed_s, 2),
        "dashboard_load_cold_ms": round(cold_total, 1),
        "worksheet_cold_p50_ms": round(statistics.median(cold_per), 1),
        "worksheet_cold_max_ms": round(max(cold_per), 1),
        "engine_query_p50_ms": round(statistics.median(cold_server), 1),
        "dashboard_load_cached_ms": round(warm_total, 1),
        "filter_change_reload_ms": round(filt_total, 1),
        "filter_worksheet_p50_ms": round(statistics.median(filt_per), 1),
        "cross_filter_reload_ms": round(xf_total, 1),
        "concurrency_threads": args.threads,
        "concurrency_requests": len(latencies),
        "concurrency_errors": errors,
        "concurrency_p50_ms": round(pct(latencies, 50), 1),
        "concurrency_p95_ms": round(pct(latencies, 95), 1),
        "concurrency_throughput_rps": round(len(latencies) / wall, 1),
        "peak_rss_mb": round(rss_mb, 1),
    }
    width = max(len(k) for k in results)
    for k, v in results.items():
        print(f"{k.ljust(width)}  {v}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
