# Implementation Plan — US-102 (AIH-102) URL Shortener API

This is an ordered delivery plan for a Python 3.12 + FastAPI implementation. Each task states the FSD functional requirements it implements and its completion check. No task includes UI work; the release is API-only.

## Tasks

### T-001 — Establish the application contract and configuration

**Implements:** FR-001, FR-006, FR-009, FR-013

Create the FastAPI application factory, typed Pydantic request/response models, settings for `BASE_URL` and the 64 KiB limit, UTC timestamp serialisation, request-ID middleware, and test fixtures that construct a fresh application/store per test. Define the FR-009 error envelope and central exception handlers, including normalisation of practical framework validation errors. Keep route registration modular so fixed routes can precede the code route.

**Done when:** The app starts under one Uvicorn worker; OpenAPI generation succeeds; error bodies use the required envelope; tests can reset storage between cases.

**Depends on:** None.

### T-002 — Implement URL validation

**Implements:** FR-002, FR-009

Add a validator that accepts exactly absolute `http`/`https` URLs with a non-empty host, up to 2,048 characters, and no leading/trailing whitespace or ASCII control characters. Reject unsupported or missing schemes and host-less values with `400` and `error.code = "invalid_url"`. Preserve the submitted URL byte-for-byte otherwise; perform no normalisation. Ensure invalid input cannot reach storage or consume a code.

**Done when:** Every accepted/rejected example in FR-002 has a focused test, including the full rejection matrix, and rejection leaves storage unchanged.

**Depends on:** T-001.

### T-003 — Implement secure code generation and collision handling

**Implements:** FR-003, FR-008

Generate codes with `secrets` from the 62-character alphanumeric alphabet in exactly 7 characters. Exclude `links`, `health`, `docs`, `redoc`, and `openapi.json` by never issuing them. Generate and insert under the store lock, regenerating if a code already exists. Add unit tests for format, alphabet, and deterministic collision-retry behaviour using an injectable generator.

**Done when:** Generated codes match `^[A-Za-z0-9]{7}$`, reserved segments cannot be issued, and a simulated collision produces a different code without exposing the collision to the caller.

**Depends on:** T-001.

### T-004 — Implement the in-memory link store

**Implements:** FR-003, FR-005, FR-011, FR-012

Implement `LinkRecord` and a store exposing atomic create, exact case-sensitive lookup, and click-increment operations. Guard mutation and snapshots with one `threading.RLock`. Preserve separate records for repeated submissions of the same URL. Avoid per-request retained state and define process-lifetime-only behaviour in the store contract. Add direct store tests for independent records, exact case sensitivity, atomic increments, and restart-equivalent reinitialisation.

**Done when:** Concurrent increments to one record are not lost, two links for one URL remain independent, a new app/store instance cannot resolve old codes, and lookups are O(1) average.

**Depends on:** T-003.

### T-005 — Implement `POST /links`

**Implements:** FR-001, FR-002, FR-003, FR-009, FR-010, FR-011

Wire the create route to enforce `Content-Type: application/json`, the 64 KiB body limit, and URL validation before generating and storing a record. Ignore unknown JSON fields. Return `201` with `code`, `long_url`, absolute `short_url`, and UTC `created_at`; ensure no record is created on any error. Map content-type failures to `415`, oversized bodies to `413`, malformed/missing/blank fields to `422`, and invalid URLs to `400`, all using the error envelope. Repeated submissions must return distinct codes and independent records.

**Done when:** Integration tests cover the success contract, every FR-001/FR-002/FR-010 rejection, unknown fields, duplicate submissions, and immediate resolvability.

**Depends on:** T-002, T-004.

### T-006 — Implement `GET /{code}` redirects and click counting

**Implements:** FR-004, FR-005, FR-007

Add the code route with an exact 7-character base62 path constraint or explicit validation. On a known code, atomically increment clicks and set `last_clicked_at`, then return `302` with `Location` equal to the stored `long_url` and `Cache-Control: no-store`. On unknown or malformed codes, return `404` with no `Location`; never echo the requested value into a redirect. Ensure methods other than `GET`, including `OPTIONS`, return `405` with the FR-009 envelope.

**Done when:** Tests prove exact `Location` equality, `no-store`, fresh `clicks = 0`, three redirects producing `clicks = 3`, unknown/malformed 404s without `Location`, and 405 for disallowed methods.

**Depends on:** T-004, T-005.

### T-007 — Implement `GET /links/{code}/stats`

**Implements:** FR-005, FR-006, FR-009

Add the fixed stats route before the code route. Return `code`, `long_url`, `clicks`, `created_at`, and `last_clicked_at` from a consistent locked snapshot; return `200` for known codes and `404` with `error.code = "not_found"` for unknown or case-mismatched codes. Ensure stats reads never mutate the record.

**Done when:** Tests verify the initial zero count, the count after redirects, `null`/populated `last_clicked_at`, case sensitivity, unknown-code 404, and no mutation from repeated stats calls.

**Depends on:** T-004, T-006.

### T-008 — Finalise routing precedence, health, and documentation paths

**Implements:** FR-003, FR-007, FR-008

Register fixed routes (`/links`, `/links/{code}/stats`, `/health`, `/docs`, `/redoc`, `/openapi.json`) before the variable code route. Implement `GET /health` as `200 {"status":"ok"}`. Confirm the reserved segments cannot be generated or returned as codes and that malformed multi-segment or encoded values never resolve. Normalise method-not-allowed responses where practical.

**Done when:** Health and OpenAPI routes remain reachable, no reserved segment is issued as a code, and routing tests confirm fixed-route precedence and 404/no-redirect behaviour.

**Depends on:** T-005, T-006, T-007.

### T-009 — Complete error and request-limit hardening

**Implements:** FR-001, FR-005, FR-009, FR-010

Audit every documented error status and map it to the FR-009 envelope with an allowed machine code. Verify the 64 KiB body limit is enforced before parsing/creation, URL length remains independently limited to 2,048 characters, and error messages contain no stack traces, file paths, or internal identifiers. Preserve atomic click increments and ensure rejected requests neither create links nor change counts.

**Done when:** Every documented status (`400`, `404`, `405`, `413`, `415`, `422`, `500` where applicable) has an asserted response, and oversized or malformed requests leave the store and statistics unchanged.

**Depends on:** T-005, T-006, T-007, T-008.

### T-010 — Enforce lifecycle and single-process semantics

**Implements:** FR-011, FR-012

Document and verify that a new application instance starts with an empty store, old codes return `404`, repeated URLs remain independent, and the deployment configuration uses one Uvicorn worker. Ensure no module-level mutable state survives the test fixture reset or falsely implies durability.

**Done when:** A restart simulation shows previously issued codes are unknown; duplicate URLs remain separately addressable and independently counted; deployment documentation states the single-worker constraint.

**Depends on:** T-004, T-005.

### T-011 — Build the complete pytest acceptance suite

**Implements:** FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013

Add pytest tests using httpx/TestClient against the real application object. Name at least one test per FR with its FR ID. Use application/store reset fixtures for isolation. Cover happy paths, every FR-002 rejection case, all error statuses, unknown/malformed codes, reserved routes, duplicate URLs, restart semantics, request limits, and 100 parallel redirects. Keep the suite offline and deterministic.

**Done when:** `pytest -q` passes offline; no test depends on another test's state; concurrent click increments are complete; and the FR-to-test mapping covers all FR-001 through FR-013.

**Depends on:** T-001 through T-010.

### T-012 — Add deployment notes and run final verification

**Implements:** FR-008, FR-012, FR-013

Document the one-worker Uvicorn command, `BASE_URL`, non-durable storage, health check, and the absence of UI/authentication. Review generated OpenAPI, run the full test suite and a local startup smoke check, and confirm all FRs and NFR-linked design notes have corresponding verification evidence. Record any residual risks (memory growth, no rate limiting, state loss on restart) for the release decision.

**Done when:** Startup, `/health`, `/docs`, `/openapi.json`, and representative create/redirect/stats requests work in a smoke run; `pytest -q` passes; the deployment note states the single-worker, non-durable constraint.

**Depends on:** T-011.

## Requirement Coverage

| FR | Implementing task(s) |
|---|---|
| FR-001 | T-001, T-005, T-009, T-011 |
| FR-002 | T-002, T-005, T-011 |
| FR-003 | T-003, T-004, T-005, T-008, T-011 |
| FR-004 | T-006, T-011 |
| FR-005 | T-004, T-006, T-007, T-009, T-011 |
| FR-006 | T-001, T-007, T-011 |
| FR-007 | T-006, T-008, T-011 |
| FR-008 | T-003, T-008, T-012, T-011 |
| FR-009 | T-001, T-002, T-005, T-007, T-009, T-011 |
| FR-010 | T-005, T-009, T-011 |
| FR-011 | T-004, T-005, T-010, T-011 |
| FR-012 | T-004, T-010, T-012, T-011 |
| FR-013 | T-001, T-011, T-012 |

## Delivery Gates

1. **Contract gate:** Typed models, error envelope, and OpenAPI schema reviewed after T-001.
2. **Core behaviour gate:** Create, redirect, stats, and health paths pass integration tests after T-008.
3. **Hardening gate:** Error matrix, limits, routing precedence, and lifecycle checks pass after T-010.
4. **Release gate:** Full offline suite passes and all FR coverage is demonstrated after T-012.
