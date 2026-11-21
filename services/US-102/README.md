# URL Shortener API

A small Python 3.12 + FastAPI service for creating short links, redirecting visitors, and reading click statistics. Links are stored in memory for the lifetime of one Uvicorn worker.

## Run locally

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

The API is available at `http://localhost:8000`. Interactive documentation is at `/docs` and the OpenAPI schema is at `/openapi.json`.

Set `BASE_URL` to control the absolute `short_url` returned by `POST /links`:

```sh
BASE_URL=https://sho.rt uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Run with Docker

```sh
docker build -t url-shortener .
docker run --rm -p 8000:8000 -e BASE_URL=http://localhost:8000 url-shortener
```

## API

Create a link:

```sh
curl -i -X POST http://localhost:8000/links \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/a/very/long/path?x=1"}'
```

Resolve a code without following the redirect:

```sh
curl -i http://localhost:8000/Ab3xY9Q
```

Read statistics:

```sh
curl http://localhost:8000/links/Ab3xY9Q/stats
```

Health check:

```sh
curl http://localhost:8000/health
```

## Limits and operation

- Requests to `POST /links` must use `Content-Type: application/json` and be no larger than 64 KiB.
- URLs must be absolute `http` or `https` URLs with a host and at most 2,048 characters.
- Errors use `{"error": {"code": "...", "message": "..."}}`.
- Redirects use HTTP `302` with `Cache-Control: no-store` so clicks are counted.
- Storage is non-durable and process-local. Run exactly one Uvicorn worker; restarting loses all links.
- There is no authentication, rate limiting, link editing, deletion, expiry, or UI in this release.

## Test

```sh
pytest -q
```
