# Health readiness consolidation TDD evidence

## User journey

As an operator, I want the stock API health endpoint to include its database
readiness check so that deployment succeeds only when the service can serve
database-backed requests.

## Evidence

| Guarantee | Test or command | Result |
|---|---|---|
| `GET /stock/api/health` executes a database check and returns 200 when it succeeds | `uv run pytest tests/api/test_health.py -q` | RED before implementation: the database session was not called; GREEN after implementation |
| `GET /stock/api/health` returns the standard 503 error envelope when the database is unavailable | `uv run pytest tests/api/test_health.py -q` | RED before implementation: returned 200; GREEN after implementation |
| `GET /stock/api/ready` no longer exists | `uv run pytest tests/api/test_health.py -q` | RED before implementation: returned 503; GREEN after implementation: returns 404 |
| Existing stockapp behavior still passes its automated suite | `uv run pytest` | 147 passed, 1 skipped |
| The Dockerfile remains structurally valid after adding its healthcheck | `docker build --check .` | PASS, no warnings |

The initial focused RED run reported 3 failed and 2 passed tests. The focused
GREEN run reported 5 passed tests.

## Coverage and known gaps

`pytest-cov` is not installed in this repository, so a numeric coverage report
could not be produced without changing project dependencies. The complete test
suite passed, apart from the existing live integration test that is skipped by
default.

The full Docker image build reached dependency installation but could not
download a PyPI wheel because the container did not trust the environment's
TLS issuer (`UnknownIssuer`). This failure occurred before project source was
built and is unrelated to the healthcheck change.

No TDD checkpoint commits were created because the working tree already
contains unrelated user-owned untracked files and no commit was requested.
