# Setup, operations and performance

## Local development
```bash
# Backend (Python 3.11+)
cd apps/api
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"    # or: python -m venv .venv && pip install -e ".[dev]"
.venv/bin/intelliq dev            # creates admin + synthetic sample content, serves :8000
                                  # prints a generated admin password unless INTELLIQ_DEV_ADMIN_PASSWORD is set

# Frontend (Node 20+)
cd apps/web && npm install && npm run dev     # http://localhost:5173, proxies /api to :8000
```
Single-origin (production-like): `npm run build` in `apps/web`, then start the API with
`INTELLIQ_WEB_DIST=apps/web/dist`.

## Tests
| Suite | Command | Notes |
|---|---|---|
| Backend unit + integration | `cd apps/api && .venv/bin/python -m pytest` | 380 tests; with no `INTELLIQ_TEST_*` engine configured, 328 pass and the 52 non-DuckDB contract tests skip |
| Connector contract vs another engine | `INTELLIQ_TEST_POSTGRES='{"host":…,"port":5432,"database":…,"user":…,"password":…}' pytest tests/integration/test_connector_contract.py` | Seeds schema `main`; same for `INTELLIQ_TEST_SQLSERVER/SNOWFLAKE/DATABRICKS` (seeding implemented for PostgreSQL) |
| Lint | `.venv/bin/ruff check intelliq tests` | |
| Frontend unit | `cd apps/web && npm test` | Vitest |
| Type check | `cd apps/web && npm run typecheck` | |
| End-to-end | `npm run web:build && npm run e2e` (repo root) | Playwright; starts an isolated server; set `PW_CHROMIUM_PATH` to use a pre-installed Chromium |
| Validation case | `intelliq validate case.json --report out.json` | See `07-validation.md` |

## Configuration (environment)
| Variable | Default | Purpose |
|---|---|---|
| `INTELLIQ_DATA_DIR` | `./.intelliq` | Metadata DB, sample warehouse, dev key |
| `INTELLIQ_SECRET_KEY` | — (required outside dev) | Fernet key for credentials at rest (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `INTELLIQ_DEV` | off | Development mode (generated key file, sample content via `intelliq dev`) |
| `INTELLIQ_COOKIE_SECURE` | off | Set `1` behind HTTPS |
| `INTELLIQ_SESSION_HOURS` | 12 | Session lifetime |
| `INTELLIQ_WEB_DIST` | — | Serve the built web app from the API |
| `INTELLIQ_MAX_UPLOAD_MB` | 100 | Workbook upload limit |
| `INTELLIQ_QUERY_RATE` / `INTELLIQ_LOGIN_RATE` | 240 / 10 per minute | Rate limits |
| `INTELLIQ_DUCKDB_ROOTS` | — | Extra directories DuckDB connections may open |

## Deployment outline
- Run `intelliq init --admin-email … --admin-password …` once, then
  `intelliq serve --host 0.0.0.0 --port 8000` behind a TLS-terminating reverse proxy
  with `INTELLIQ_COOKIE_SECURE=1` and `INTELLIQ_SECRET_KEY` from a secret manager.
- Install only the connector extras you need: `pip install "intelliq[postgres,sqlserver,snowflake,databricks]"`.
- Back up `INTELLIQ_DATA_DIR/metadata.sqlite3` (SQLite WAL — use the online backup API or a filesystem snapshot).
- **Scale out with several single-worker processes behind the proxy/load balancer**
  (see measurements below for why not `--workers N` in this environment). Result caches
  are per process; the metadata store is shared.

## Observability
- Structured access logs (uvicorn) and application logs (`intelliq.*` loggers); data-source
  errors are logged with dataset id, never with credentials or values.
- Every query is audited with dataset, row count, duration and cache hit; `/api/admin/system`
  exposes cache statistics; `/api/health` for liveness.
- Not yet built: metrics endpoint (Prometheus), tracing, log shipping.

## Performance measurements
Environment: cloud container, 4 vCPU, Python 3.13, DuckDB 1.5, synthetic sample warehouse
(~12k order lines). Measured 2026-10-20. **These characterise engine and API overhead on
small data; they are not capacity figures for production warehouses**, where query time is
dominated by the database.

Real HTTP (uvicorn, single worker, keep-alive client in a separate process):

| Metric | Result |
|---|---|
| Dashboard first load (10 worksheets, sequential, uncached) | 207 ms total |
| Same dashboard, cached | 104 ms total |
| Filter change → all worksheets reloaded | 176 ms total |
| Health endpoint p50 | 2.1 ms |
| Cached worksheet request p50 | 9.4 ms |
| Uncached worksheet request p50 / p95 (1 client) | 16.4 / 23.5 ms |
| Uncached throughput, 4 / 8 / 16 concurrent clients | 47.8 / 42.1 / 38.6 req/s (p95 91 / 202 / 425 ms) |
| Server RSS after load | ~140 MB |

Component timings per request (in-process): planning 1.2 ms, cached execution 1.6 ms,
spec load/validation 0.4 ms, dataset load 0.2 ms, audit write 0.6 ms, engine query p50
≈ 5 ms. A single process is CPU-bound at ~40–48 req/s.

Optimisation driven by measurement: metadata reads were taking SQLite's write lock
(`BEGIN IMMEDIATE`); reads now use deferred transactions (WAL snapshots), which reduced
in-process p95 under 8-way concurrency from 455 ms to 276 ms.

Multi-process note: `intelliq serve --workers 4` doubled peak throughput (88 req/s at 16
clients) but added a constant ~42 ms to every request in this container — including the
trivial health endpoint — which points to a transport artefact of uvicorn's multi-process
mode here rather than application cost (not yet diagnosed). Hence the recommendation to
scale with single-worker processes behind a load balancer.

Bundle: the web app's main chunk is 137 kB (42 kB gzip) plus React 167 kB (55 kB gzip);
ECharts (tree-shaken core with the 5 chart types and components used) is 574 kB
(192 kB gzip) and is cached; authoring and admin pages are lazy-loaded so viewers never
download them.

Reproduce: `python apps/api/scripts/benchmark.py` (in-process) and the HTTP load script
pattern described above.
