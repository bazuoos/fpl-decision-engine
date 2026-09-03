# RFC 0026A: Web product architecture

- Status: Proposed for independent review
- Scope: Web presentation and application architecture
- Date: 2026-09-03
- Decision authority: Existing immutable Engine v1 artifacts

## 1. Summary

Build the web product as an untrusted presentation and application layer around the existing trusted Engine v1 pipeline. The browser never calculates projections, transfer legality, squad legality, XI selection, captaincy, reliability, or decision differences. A small Python API authenticates users, invokes the existing operational workflow through a narrow command boundary, and returns only artifacts that have passed the existing trust-chain validators.

The initial product can serve one private user from a single host and filesystem. Its public contracts, identity model, and storage interfaces nevertheless avoid assumptions that would prevent stateless API replicas, object storage, PostgreSQL, background workers, and tenant isolation later.

The recommended stacks are:

- Frontend: React, TypeScript, and Vite.
- API/application layer: Python 3.10+ and FastAPI.
- Mutable application metadata: no database in the first read-only slice; PostgreSQL when authenticated writes are introduced.
- Immutable artifacts and evidence: the current filesystem layout behind an adapter initially; S3-compatible object storage with versioning and encryption when deployed for multiple users.

No production pipeline code is changed by this RFC.

## 2. Repository findings

The repository is a Python 3.10+ package installed with `python -m pip install --editable .`. It uses `unittest`, DuckDB, HiGHS, and JSON Schema, and CI runs the complete offline suite plus `git diff --check`.

The trusted boundaries already exist:

- `decision.py` owns squad, XI, formation, captain, and vice-captain legality, including `validate_decision_selection(...)`.
- `transfer_decision.py` owns the versioned one-transfer decision and immutable candidate outputs.
- `decision_reliability.py` owns diagnostic reliability output without changing the official optimization result.
- `presentation/gameweek_decision.py` builds and validates `GameweekDecision` v1 and delegates persisted selection validation to the trusted core.
- `operational_manifest.py` defines canonical JSON, deterministic semantic IDs, exact-field validation, and preparation/final manifest contracts.
- `operational_runner.py` implements the two-phase preparation and verified-manager-state workflow with immutable publication.
- `decision_journal.py` anchors prospective human decisions and outcomes to completed Engine v1 evidence.
- `decision_diff.py` revalidates both completed trust chains before constructing a read-only comparison.

The repository has no web frontend, HTTP API, user account model, or application database today. Generated production data is intentionally outside the committed test suite. This is the right point to add a web seam, not a second engine.

## 3. Non-negotiable trust invariants

1. A verified immutable Engine v1 artifact is the only authority for an engine decision.
2. The browser and API do not reconstruct FPL rules, projections, transfer candidates, optimized lineups, captaincy, reliability, or decision diffs.
3. The API may invoke existing production entry points and validate their outputs; it may not reimplement their semantics.
4. A record is not displayable as a trusted recommendation until its complete artifact chain verifies.
5. Human manager state is fresh, explicit, evidence-backed, and bound to one user, season, entry, target gameweek, and preparation.
6. FPL entry ID is a public domain identifier, not an authentication credential.
7. “Latest” is a convenience for browsing only. It is never an identity or an authority input.
8. Research data has a one-way, consent-gated path out of production. Production has no runtime dependency on research outputs.
9. A research result can enter production only through a preregistered experiment, review, a new explicit model/version, and the normal code-release process.
10. All time-sensitive gates use trusted server UTC time and the deadline already frozen in Engine v1 evidence, never browser time.

## 4. System context

```text
                                       TRUSTED DECISION PLANE
                             +---------------------------------------+
                             | Existing fpl_decision_engine package |
                             | refresh -> features -> xFP ->         |
                             | optimizer -> reliability -> contract  |
                             +------------------+--------------------+
                                                |
                                      canonical immutable artifacts
                                                |
                                                v
+---------+  HTTPS  +-------------+    +--------------------+    +----------------+
| Browser | <-----> | Web/API app | -> | Trusted artifact   | -> | Artifact store |
| React   |         | FastAPI     |    | reader/command seam|    | filesystem/S3  |
+---------+         +------+------+    +--------------------+    +----------------+
                            |
                            +---- mutable account, consent, and operation metadata
                            |                         |
                            |                         v
                            |                    +----------+
                            |                    | Postgres |
                            |                    +----------+
                            |
                            +---- one-way consent-gated publication
                                                      |
                                                      v
                                            +--------------------+
                                            | RESEARCH PLANE     |
                                            | pseudonymous data  |
                                            | frozen experiments |
                                            +--------------------+
```

The web/API box is outside the trusted decision plane. “Trusted” in `Trusted artifact reader` describes validation of evidence produced by the engine; it does not grant the web layer authority to create equivalent results.

## 5. Dependency rule and module layout

The dependency direction is one-way:

```text
web/ -> HTTP contracts -> fpl_decision_app -> public trusted-engine facade
                                                |
                                                v
                                  existing fpl_decision_engine

existing fpl_decision_engine -X-> fpl_decision_app
existing fpl_decision_engine -X-> web/
```

Proposed repository layout:

```text
docs/rfcs/                         architecture decisions
src/fpl_decision_engine/           existing trusted engine; preserved
src/fpl_decision_app/              future API/application package
  api/                              HTTP routes, auth, problem responses
  application/                      use-case orchestration only
  artifacts/                        artifact-store adapter and verified reader
  identity/                         user/team authorization model
  research_export/                  one-way consent-gated publisher
contracts/api/v1/                  checked OpenAPI and example payloads
web/                               React/TypeScript/Vite application
  src/api/                          generated/typed API client
  src/features/                     presentation features
  src/components/                   reusable visual components
tests/                              current engine tests plus app contract tests
```

`fpl_decision_app` may depend on `fpl_decision_engine`; the reverse import is prohibited. Runtime web code must not import DuckDB, HiGHS, feature generation, prediction generation, or optimizer modules directly. Those are reachable only through the existing operational workflow or the verified artifact reader.

Some reusable trust-chain functions are currently private helpers, notably completed-evidence and preparation-directory validation. Task026B should expose a narrow public facade that delegates to those implementations. It must not copy their logic or weaken validation.

## 6. Frontend decision

Use React with TypeScript and Vite as a client-rendered application.

Why:

- The product is an authenticated application, not a search-indexed content site, so server rendering is not required initially.
- Static assets can be served independently from the Python API.
- TypeScript can consume versioned OpenAPI types and make absent/invalid states explicit.
- Vite keeps the first vertical slice small and does not introduce server-side JavaScript as another application authority.

Use React Router for navigation and TanStack Query for server-state fetching, invalidation, and explicit loading/error states. Keep decision payloads immutable in frontend state. UI-derived values are limited to formatting, sorting copies for display, and accessibility labels; no numerical or legality calculations affect the presented recommendation.

Do not introduce a broad component framework in Task026B. Start with semantic HTML, accessible form controls, a small token-based stylesheet, and focused components. The application must work with keyboard navigation, screen readers, narrow screens, and reduced motion.

## 7. Backend and application decision

Use FastAPI on Python 3.10+, matching the repository's language and supported Python boundary.

Why:

- It can call the existing Python operational workflow without shell-output parsing.
- Pydantic/OpenAPI provide an explicit HTTP contract while existing JSON Schemas continue to govern artifact payloads.
- ASGI supports a small synchronous deployment now and asynchronous operation submission later.
- Keeping the application layer in Python reduces cross-language duplication at the trust boundary.

The API has two kinds of use case:

1. **Read:** resolve an explicit semantic ID, validate the complete evidence chain, and return a versioned read envelope containing the canonical artifact payload.
2. **Command:** authorize an explicit request and invoke the existing Engine v1 Phase 1 or Phase 2 workflow. The API reports status and IDs; it never synthesizes the result.

Long-running refresh/preparation commands eventually run in a worker using the same application service and service identity. The worker calls the existing trusted workflow. Moving execution out of the HTTP process must not change any semantic input, ID, timestamp rule, or artifact bytes.

## 8. API and read-model contracts

### 8.1 Versioning

- Prefix HTTP routes with `/api/v1`.
- Include `api_version` in response envelopes.
- Preserve each embedded artifact's existing `schema_name`/`schema_version` or version identifier.
- Treat additive optional API fields as compatible. A removed field, changed meaning, changed null behavior, or tightened enum requires `/api/v2`.
- Never change an existing artifact schema in place. Add a new artifact version and an explicit read adapter.
- Check the generated OpenAPI document into `contracts/api/v1/` and make CI detect unreviewed drift.

HTTP version and artifact version are independent. API v1 may serve several explicitly supported immutable artifact versions; unsupported versions fail closed.

### 8.2 Read envelope

The API should return the verified artifact without restating decision values elsewhere:

```json
{
  "api_version": "1.0",
  "artifact": {
    "artifact_type": "GameweekDecision",
    "artifact_version": "1.0.0",
    "semantic_id": "decision_...",
    "sha256": "...",
    "payload": {}
  },
  "verification": {
    "state": "VERIFIED",
    "verified_at": "server execution metadata, not artifact evidence"
  }
}
```

`payload` is the validated, parsed canonical artifact. The API does not independently expose a second `recommended_action`, objective, XI, or captain field. `verification.verified_at` is mutable operational metadata and never participates in an Engine v1 identity or evidence cutoff.

Use strong ETags derived from artifact SHA-256 and `Cache-Control: private, immutable` for immutable, user-authorized responses.

### 8.3 Initial endpoints

Read-only Task026B endpoints:

```text
GET /api/v1/health
GET /api/v1/preparations/{preparation_id}
GET /api/v1/decisions/{decision_id}
GET /api/v1/decision-diffs/{decision_diff_id}
GET /api/v1/journals/{journal_entry_id}
GET /api/v1/outcomes/{outcome_id}
```

Later command endpoints:

```text
POST /api/v1/preparations
POST /api/v1/preparations/{preparation_id}/manager-evidence
POST /api/v1/decisions/{decision_id}/journal-entries
POST /api/v1/journal-entries/{journal_entry_id}/outcomes
GET  /api/v1/operations/{operation_id}
```

Commands accept explicit season, gameweek, and semantic IDs. They never accept `latest`. A 202 response may contain a mutable `operation_id`; completed semantic IDs come only from the engine. An idempotency key protects browser retries but is execution history, not manifest input.

### 8.4 Error contract

Use RFC 9457-style problem details with stable application error codes, for example:

- `ARTIFACT_NOT_FOUND`
- `ARTIFACT_HASH_MISMATCH`
- `ARTIFACT_SCHEMA_UNSUPPORTED`
- `TRUST_CHAIN_INVALID`
- `COMPARISON_SCOPE_MISMATCH`
- `VERIFIED_MANAGER_STATE_REQUIRED`
- `MANAGER_STATE_MISMATCH`
- `DEADLINE_PASSED`
- `IMMUTABLE_PUBLICATION_CONFLICT`
- `OPERATION_IN_PROGRESS`
- `UPSTREAM_UNAVAILABLE`
- `UNAUTHORIZED`
- `FORBIDDEN`

Errors contain a correlation ID but no absolute filesystem path, stack trace, screenshot metadata, auth token, or manager evidence content.

## 9. Artifact loading and verification

The application resolves semantic IDs through an artifact index/store adapter. It never accepts a browser-supplied filesystem path or object-store key.

Read sequence:

```text
explicit ID
   -> authorize user against artifact ownership/scope
   -> resolve expected identity-addressed object
   -> read bytes once
   -> verify SHA-256 and canonical representation
   -> validate schema/exact fields
   -> re-anchor upstream hashes and rebuild where existing validators require it
   -> return verified immutable payload
```

The facade reuses the repository's existing validators for preparations, completed evidence, GameweekDecision, journals/outcomes, and DecisionDiff. If a complete chain cannot be proven, it returns no recommendation payload.

Reading bytes once before parsing avoids a check-then-read race. Filesystem publication retains the existing temp-file, file-fsync, atomic-rename behavior and its documented power-loss limitation. In object storage, publish to a content-addressed key using conditional create, store the digest as object metadata, and enable bucket versioning/object retention as appropriate.

The application may maintain an index of semantic ID to object URI and expected hash. That index accelerates discovery; it is not authority. A matching index row without matching verified bytes is an error.

## 10. User and FPL team identity

Use separate identities:

```text
AppUser
  user_id: internal opaque UUID
  auth_subject: unique (issuer, subject)

FplTeamConnection
  connection_id: internal opaque UUID
  user_id
  season
  entry_id: official public FPL entry identifier
  verification_method/version
  status
```

The same person may have multiple entries or seasons. Element IDs identify players within the relevant frozen FPL universe; they are not user identities. Do not join user accounts, entries, seasons, or players by display name.

Enforce one active owner per `(season, entry_id)` unless an explicit shared-team feature is designed later. The public entry endpoint can help reconcile a squad but cannot prove control. Team connection verification requires a manager-controlled, fresh action/evidence mechanism. Until such a mechanism is built, the product remains private/invite-only and treats ownership as manually administered.

Every preparation, manager evidence submission, final decision, journal, and outcome is authorized through `connection_id` and checked against its frozen entry ID/season/gameweek. API responses do not expose cross-tenant artifacts even when a semantic ID is guessed.

## 11. Manager-state verification UX

The UX mirrors the existing two-phase operational gate:

```text
Choose explicit GW
    -> Phase 1 produces immutable preparation ID and deadline
    -> UI shows “manager state required”
    -> user uploads fresh official evidence and reviews extracted fields
    -> UI requires confirmation of all 15 players, bank, free transfers,
       transfer cost, chip state, and manager-specific selling prices
    -> server binds evidence to user/entry/preparation and uses its own UTC clock
    -> existing Phase 2 validates and freezes the decision
    -> UI displays only the verified GameweekDecision
```

Screenshots are evidence, not executable instructions. OCR may propose values but is untrusted convenience; the user must review each field. The UI displays source SHA-256, capture/upload provenance, target gameweek, deadline, and a 15-of-15 reconciliation before enabling submission.

Manager state is never silently carried from a prior gameweek. Current price is never substituted for selling price. If screenshot and official frozen snapshot identities/positions conflict, if a field is unreadable, if the evidence is bound to another preparation, or if the server clock is at/after the deadline, Phase 2 fails closed with a precise state.

No FPL password, authenticated FPL session cookie, or browser session export is collected.

## 12. Authentication and authorization boundary

Use a standards-based OpenID Connect provider. The browser uses Authorization Code with PKCE. For the web session, prefer a same-origin backend session held in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie rather than tokens in local storage.

- Mutating requests require CSRF protection.
- API CORS is deny-by-default with an explicit production origin allowlist.
- Server sessions are short-lived and revocable; step-up authentication may protect export/deletion later.
- Authorization is enforced in the API/application service, never only in React.
- Admin/support access is a separate role with audited, time-limited elevation.
- The public FPL entry ID is never accepted as proof of identity.

For Task026B's local read-only slice, bind the API to localhost and use a development-only local identity adapter. Do not expose that mode on a network or encode it as a production bypass.

## 13. Storage strategy

Keep four stores logically separate.

| Store | Purpose | Initial form | Multi-user form | Authority |
|---|---|---|---|---|
| Engine artifact store | Immutable preparations, decisions, reliability, diffs, journals, outcomes | Existing filesystem tree | Private versioned S3-compatible bucket | Artifact bytes plus verified trust chain |
| Application metadata | Users, team connections, consent, artifact index, operation status, deletion/export jobs | None for read-only slice | PostgreSQL | Operational only; never decision values |
| Evidence store | Manager screenshots and structured submissions | Private filesystem outside static root | Separate encrypted private bucket | Evidence input, not decision output |
| Research store | Pseudonymous prospective observations and frozen datasets | Absent | Separate database/bucket/account | Research only |

Do not use SQLite as a production stepping stone. It adds a migration without solving authentication, concurrent writes, or tenant isolation. Task026C can introduce PostgreSQL when the first authenticated write exists. Use database constraints and application authorization together, with row-level security as defense in depth.

Artifact immutability means bytes are never silently replaced while retained. It does not override a user's right to delete personal data. Deletion removes eligible personal objects and their identifying index entries; subsequent reads return an explicit deleted/unavailable state rather than cached content. A non-identifying deletion receipt may remain for audit.

## 14. Privacy, consent, export, and deletion

Collect only what is required for the declared feature. Treat screenshots, team composition, manual prices, journal reasons, and the link between an app user and an FPL entry as personal data even where portions are publicly observable.

Required controls:

- Separate service terms/privacy acknowledgement from optional research consent.
- Consent is explicit, scoped, versioned, timestamped, withdrawable, and off by default.
- The service remains usable when research consent is declined.
- Define retention separately for raw screenshots, structured manager state, operational artifacts, logs, and research exports.
- Encrypt private data in transit and at rest; keep object keys opaque.
- Export produces a machine-readable archive of account/team links, consent history, submitted evidence (when requested), canonical artifacts, and hashes.
- Deletion revokes sessions, deletes or tombstones mutable account rows, deletes eligible evidence and personal artifacts, and sends a deletion request to the research plane where consent terms require it.
- Logs and metrics exclude entry IDs, names, screenshot paths, free-text reasons, and raw evidence.

Where deletion makes an old artifact trust chain unavailable, the UI must say so. It must not return an unverified cached recommendation.

## 15. Prospective user decision records

Task024 `DecisionJournalEntry` and `DecisionOutcome` remain the operational record. Do not add browser-only analytics events to those immutable contracts.

With explicit research consent, a one-way publisher may create a separate, append-only research record such as `prospective-decision-observation-v1` containing:

- a research-specific pseudonymous subject ID;
- hashes/IDs of the verified journal and decision, not mutable restatements;
- preregistered cohort and schema versions;
- declared human action and override classification sourced from the journal;
- outcome reference only after the trusted outcome exists;
- consent version and export timestamp in research provenance.

The research subject ID is derived with a research-owned secret or lookup table and cannot be joined by public entry ID. Withdrawal and deletion use a separately protected re-identification map. Analysts do not receive that map.

Historical backfills remain explicitly distinguishable and must not enter a prospective cohort. A failed prospective gate cannot be repaired by relabeling later evidence.

## 16. Population-evidence architecture

```text
PRODUCTION                                      RESEARCH
verified journal/outcome
       |
       +-- consent and eligibility check
       +-- one-way export job
       +-- minimize/pseudonymize ------------> append-only landing zone
                                                -> frozen dataset manifest
                                                -> preregistered experiment
                                                -> independent review
                                                -> accepted/rejected result

No runtime read path <-------------------------X

Accepted result -> explicit model/version design -> code review -> normal release
```

Production service credentials can write only eligible exports to the research landing zone. Research credentials cannot write production artifacts or configuration. The live engine never queries population tables, dashboards, experiment outputs, or feature flags.

A population insight reaches production only through all of these gates:

1. preregistered hypothesis, features, populations, metrics, and holdout policy;
2. immutable input/output manifests and leakage tests;
3. result review without post-hoc candidate changes;
4. an explicit new model/version proposal;
5. code, contract, test, and release review.

## 17. Caching

- Cache verified immutable artifacts by `(artifact_type, schema_version, sha256)`, never by `latest` or a mutable path.
- Revalidate authorization on every request even when bytes are cached.
- Use private immutable browser caching with ETags for user-authorized artifacts.
- Cache mutable operation status and artifact indexes briefly, with explicit invalidation after publication.
- Never place manager evidence, journal free text, sessions, or exports in a shared public cache.
- Keep negative caching short so a just-published artifact becomes visible promptly.
- A cache hit cannot bypass schema, hash, scope, or ownership validation; cache entries contain only previously verified values and their verification version.

## 18. Observability and auditability

Emit structured events with:

- request/correlation ID;
- operation ID;
- user ID in a non-reversible log-safe form;
- preparation/decision/artifact IDs where authorized;
- artifact hash prefix, validator version, duration, and stable result/error code.

Record metrics for validation failures, hash mismatches, deadline refusals, manager-state mismatches, immutable conflicts, queue depth, phase duration, cache hit rate, and API latency/error rate. Trace API-to-worker calls with correlation IDs.

Execution history—attempt count, retries, last access, cache hit, worker identity, and duration—belongs in logs or mutable operation records. It must never be inserted into semantic operational manifests, evidence cutoffs, or deterministic IDs.

Alert on repeated hash failures, cross-tenant authorization failures, abnormal evidence uploads, missed deadlines, stuck operations, and divergence between an artifact index and stored bytes.

## 19. Security model

Primary threats and controls:

| Threat | Mitigation |
|---|---|
| Forged or modified decision artifact | Complete existing trust-chain validation before display; content hashes; conditional immutable publication |
| IDOR/cross-user artifact access | Opaque internal user/connection IDs; authorization before resolution and after index lookup; tenant isolation tests |
| Path traversal/object-key injection | Accept semantic IDs only; strict ID grammar; server-side path/key construction; store-root containment checks |
| Replay or mis-bound manager evidence | Bind user, connection, entry, season, GW, preparation ID, source hash, and server verification time |
| Deadline manipulation | Server UTC clock and frozen official deadline only; clock monitoring; fail closed at/after deadline |
| Malicious screenshot/upload | Size/type limits, decode in a sandboxed worker, malware scan, randomized key, never serve inline as executable content |
| Browser/session attack | OIDC, HttpOnly cookies, CSRF tokens, CSP, same-origin policy, output encoding, rate limiting |
| Sensitive log leakage | Structured allowlist logging and automated redaction tests |
| Research-to-production feedback | Separate storage/IAM and no production read credential; reviewed version-promotion gate |
| Supply-chain compromise | Locked dependencies, reviewed updates, fresh-checkout CI, image scanning, least-privilege runtime |

Use TLS everywhere, managed secrets, encryption keys separated by environment and plane, read-only filesystem/container roots where practical, and least-privilege service roles. The public web server must not expose the artifact or evidence filesystem directly.

## 20. Deployment assumptions

### Private/single-user stage

- Static frontend on the same origin as a single FastAPI process.
- API bound to localhost or protected private ingress.
- Existing local artifact filesystem mounted read-only for read endpoints.
- A separately permissioned runner process performs commands.
- UTC-synchronized host clock.
- No application database until authenticated writes are introduced.

### Multi-user stage

- Static frontend behind a CDN/WAF.
- Stateless API replicas behind a load balancer.
- Separate worker pool and durable queue for refresh/preparation/resume operations.
- PostgreSQL for application metadata.
- Private, encrypted, versioned object stores for artifacts and evidence.
- Separate research account/project and credentials.

The store, queue, clock, identity, and runner are ports behind application interfaces from the first implementation. One host may implement several ports, but route contracts and semantic IDs do not encode host paths or process topology.

## 21. Fail-closed application states

The UI represents trust state explicitly:

```text
NOT_PREPARED
PREPARING
VERIFIED_MANAGER_STATE_REQUIRED
DECISION_RUNNING
VERIFIED_DECISION_AVAILABLE
DEADLINE_PASSED
TRUST_CHAIN_INVALID
IMMUTABLE_CONFLICT
UPSTREAM_UNAVAILABLE
DELETED_OR_UNAVAILABLE
```

Only `VERIFIED_DECISION_AVAILABLE` renders an engine recommendation as trusted. A previously verified decision may be displayed as an explicitly identified historical artifact, never as current after a newer target or preparation is selected. If a refresh fails, the product does not silently substitute stale data. If manager evidence is incomplete, the product does not estimate or carry forward missing fields.

Errors retain the last explicit user input locally only as needed to retry; they do not create a journal or final decision. A Decision Journal entry is a separate, deliberate human action after a completed decision.

## 22. Testing strategy

### Trusted engine

Keep the existing complete offline suite authoritative and unchanged. New web work must not require ignored production data or network access in CI.

### Artifact boundary

- Golden contract tests for every supported artifact/schema version.
- Adversarial tests for modified bytes, wrong hash, noncanonical JSON, unknown fields/version, broken upstream reference, scope mismatch, path escape, and check/read races.
- Verify the public reader facade gives the same accept/reject result as existing trusted validators.
- Verify API responses do not independently restate optimizer outputs.

### API/application

- Unit tests with committed minimal synthetic artifacts and fake store/clock/auth ports.
- Authorization tests across users, entries, seasons, and guessed semantic IDs.
- Idempotency, immutable conflict, exact-GW, deadline, manager-evidence, deletion, and export tests.
- OpenAPI snapshot/compatibility test.
- No live official FPL requests in committed tests.

### Frontend

- Component tests for verified, blocked, invalid, loading, stale-historical, and deleted states.
- Accessibility checks for keyboard, focus, labels, contrast, and screen-reader status announcements.
- Contract tests generated from the checked OpenAPI/schema examples.
- Playwright end-to-end tests against a local fake API serving committed synthetic artifacts.
- A guard test proving frontend bundles do not import or embed optimizer/model code.

### Deployment

- Fresh-checkout CI installs locked Python/JavaScript dependencies and runs both suites offline except dependency retrieval.
- Staging smoke tests use disposable synthetic or copied read-only evidence, never sealed holdouts.
- Backup/restore and deletion drills verify index/object consistency.

## 23. Schema and migration strategy

### API and artifacts

- Keep an explicit compatibility matrix of API version to accepted artifact versions.
- Add fields only as optional with defined absence semantics within an API major version.
- Make breaking changes in parallel `/api/v2` endpoints with a published deprecation window.
- Never rewrite old immutable artifacts to a new schema. Use an explicit pure read adapter and retain source hash/version in the response.
- Reject unknown artifact versions unless an reviewed adapter exists.
- Generate TypeScript types from the checked OpenAPI contract and fail CI on drift.

### Database

Use Alembic migrations with reviewed forward and rollback/operational plans. Prefer expand/migrate/contract deployments: add compatible structures, backfill operational metadata, switch readers/writers, then remove old structures in a later release. Database migrations never rewrite Engine v1 artifact content or IDs.

Research schemas are versioned and migrated independently. A research schema change cannot require a production engine deployment.

## 24. Evolution from one user to 100,000

The evolution changes adapters and capacity, not decision semantics:

| Stage | Users | Changes | Preserved seam |
|---|---:|---|---|
| Local private | 1 | Read-only API, local identity, filesystem artifact reader | Explicit IDs and full validation |
| Authenticated private | 1–100 | OIDC, PostgreSQL, evidence upload, command worker, consent/export/delete | User/connection IDs and application ports |
| Multi-tenant | 100–10,000 | Object storage, queue, stateless API replicas, tenant controls, rate limits | Same API/artifact contracts |
| Large service | 10,000–100,000 | Partitioned workers, CDN, read replicas, lifecycle policies, stronger isolation and SLOs | Same engine authority and one-way research boundary |

Official FPL refreshes should be deduplicated by explicit season/GW/snapshot identity where policy allows, while manager state and decisions remain user-scoped. Optimization work can fan out across workers because preparation and manager hashes yield deterministic IDs. Per-user and global rate limits protect both the service and official FPL endpoints.

## 25. ADR-style decisions

### ADR-026A-01 — Engine artifacts remain the sole decision authority

- Decision: The web displays only artifacts verified by existing trusted readers/workflows.
- Rationale: Prevents drift and preserves the reviewed legality and provenance model.
- Consequence: UI feature requests may require a new trusted presentation contract rather than a quick browser calculation.

### ADR-026A-02 — React/TypeScript/Vite frontend

- Decision: Use a static client-rendered application.
- Rationale: Fits an authenticated tool, supports typed contracts, and avoids a second server runtime.
- Consequence: SEO and server rendering are deferred; they are not current requirements.

### ADR-026A-03 — Python/FastAPI application API

- Decision: Add a separate Python application package that depends one-way on the engine.
- Rationale: Reuses trusted Python validation without shell parsing or semantic duplication.
- Consequence: Dependency boundaries require import-guard tests and careful public facades.

### ADR-026A-04 — REST with versioned envelopes and embedded canonical artifacts

- Decision: Use `/api/v1`, OpenAPI, and existing artifact schema versions.
- Rationale: Resource/command shapes are small, explicit, cacheable, and auditable.
- Consequence: GraphQL flexibility is intentionally not introduced.

### ADR-026A-05 — Content-addressed immutable artifact access

- Decision: Resolve explicit semantic IDs and verify content hashes/trust chains on every authority read.
- Rationale: Paths, indexes, and caches cannot become sources of truth.
- Consequence: Reads cost more; verified-by-hash caching contains that cost.

### ADR-026A-06 — User identity is separate from FPL entry identity

- Decision: Use internal users and season-scoped team connections.
- Rationale: FPL entry IDs are public and not proof of control.
- Consequence: A real ownership-verification flow is required before public multi-user launch.

### ADR-026A-07 — Fresh explicit manager evidence remains a hard Phase 2 gate

- Decision: Preserve screenshot/manual verification and server-clock semantics; OCR is advisory only.
- Rationale: Selling prices and editable state are manager-specific and cannot be inferred safely.
- Consequence: The first product retains a deliberate human verification step.

### ADR-026A-08 — PostgreSQL begins with authenticated writes

- Decision: Use no app database for the read-only slice, then PostgreSQL rather than a production SQLite interim.
- Rationale: Avoids an avoidable migration and supports concurrency, constraints, and tenant isolation.
- Consequence: Task026C carries more deployment setup than a local-only prototype.

### ADR-026A-09 — Research is a separate one-way data plane

- Decision: Consent-gated pseudonymous exports may leave production; research cannot be queried by the live engine.
- Rationale: Protects users and the preregistered model-development process.
- Consequence: Population learning is slower and deliberately release-mediated.

### ADR-026A-10 — Fail closed, with no authoritative `latest`

- Decision: Missing, mismatched, late, unauthorized, or unsupported evidence blocks the relevant view/command.
- Rationale: Convenience must not weaken identity, chronology, or trust-chain guarantees.
- Consequence: The UI must make blocked states useful and actionable instead of masking them.

All ADRs are proposed by this RFC and become accepted only after independent review.

## 26. Important tradeoffs

| Choice | Benefit | Cost accepted |
|---|---|---|
| Static React app instead of full-stack JavaScript | Clear Python trust boundary; simple deployment | No initial SSR |
| FastAPI instead of exposing CLI subprocesses | Typed errors/contracts and direct validator reuse | New application dependencies later |
| Verify whole chain before display | Strong provenance | Higher read latency |
| Manual manager-state confirmation | Honest selling-price/editable-state evidence | More user effort |
| PostgreSQL later, no SQLite production stage | Clean multi-user path | Authenticated write slice is not zero-infrastructure |
| Separate research plane | Privacy and methodological integrity | No online/adaptive model learning |
| Explicit IDs, never `latest` for authority | Reproducibility | More visible identity handling in UX |
| Immutable personal artifacts within retention | Auditability | Deletion needs explicit lifecycle semantics |

## 27. Major risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Application code gradually duplicates engine semantics | Conflicting recommendations | Import boundaries, contract tests, code ownership, architecture checks, no optimizer/model dependencies in web/app |
| Private helper reuse becomes brittle | Upgrades break API validation | Introduce one narrow public verified-artifact facade in Task026B, delegating to current logic |
| Screenshot verification is burdensome/error-prone | Abandonment or bad state | OCR as untrusted assistance, field-by-field confirmation, exact reconciliation, retain manual override with evidence |
| Artifact validation latency | Slow reads at scale | Cache verified content by hash; precompute non-authoritative index; never weaken verification |
| Cross-tenant data exposure | Privacy/security incident | Authorization before artifact resolution, opaque IDs, RLS defense, adversarial IDOR tests |
| Immutability conflicts with deletion | Privacy noncompliance | Document retention, delete personal objects/indexes, explicit unavailable state, non-identifying receipt |
| FPL API availability/rate limits | Preparation failures | Controlled shared refresh, backoff in existing workflow, explicit upstream error; no stale substitution |
| Clock error near deadline | Invalid prospective evidence | UTC/NTP monitoring, safety margin in UX, server-clock gate remains authoritative |
| Research data leaks into production | Methodological contamination | Separate IAM/storage/network, one-way publisher, no runtime read path, promotion checklist |
| Version proliferation | Client/operational complexity | Compatibility matrix, explicit adapters, deprecation policy, contract CI |

## 28. Phased implementation plan

### Task026B — Read-only trusted decision web slice

Goal: Prove the presentation seam without authentication, writes, or engine changes.

- Add `fpl_decision_app` with a public verified-artifact reader facade that delegates to existing validators.
- Add FastAPI health and explicit-ID read endpoints for a completed GameweekDecision and DecisionDiff.
- Add API v1 envelope/problem contracts and checked OpenAPI snapshot.
- Add a minimal React/TypeScript/Vite UI that renders verified decision, lineup, action, reliability caveats, provenance, and diff flags.
- Bind local mode to localhost; accept configured artifact root, not arbitrary paths.
- Use committed synthetic test fixtures only; add trust-chain, malformed-artifact, and no-second-source-of-truth tests.
- Add frontend/API fresh-checkout CI without live FPL access.

Exit criteria: a fresh checkout can render a synthetic verified decision and fail closed on every tampered link; existing engine outputs and tests are unchanged.

### Task026C — Authenticated manager workflow and privacy controls

Goal: Support a real private user's Phase 1/Phase 2 flow safely.

- Add OIDC/session/CSRF boundaries and PostgreSQL metadata with Alembic.
- Add user/team connection model and explicit ownership verification policy.
- Add private evidence storage, upload hardening, structured review, consent ledger, retention, export, and deletion.
- Add authorized command endpoints that invoke existing preparation/resume services with explicit IDs.
- Add operation status/idempotency and a separately permissioned worker.
- Add journal creation only as a distinct, deliberate post-decision user action.
- Add audit/metrics/redaction and tenant-isolation/security tests.

Exit criteria: one private user can complete a prospective run without CLI interaction; every displayed decision revalidates; no user can resolve another user's evidence or artifacts.

### Task026D — Multi-user and population-evidence foundation

Goal: Scale the reviewed seams and create a methodologically isolated research dataset.

- Move immutable artifacts/evidence to separate encrypted versioned object stores.
- Add stateless API replicas, durable work queue, worker scaling, lifecycle policies, backup/restore, and SLOs.
- Implement opt-in one-way pseudonymous prospective journal/outcome export.
- Add frozen research dataset manifests, deletion propagation, consent-version filters, and research IAM isolation.
- Add operational dashboards for service health only, not unreviewed model signals.
- Run load, isolation, disaster-recovery, and privacy drills.

Exit criteria: multi-tenant isolation and operational capacity are demonstrated; production has no research read path; no population result changes xFP without the existing preregistered release process.

## 29. Explicit non-goals

- Changing xFP v0.1 or adding an xFP v0.2 component.
- Reimplementing FPL scoring, transfer legality, budget/club rules, XI formation, captaincy, or optimization in the browser/API.
- Executing transfers or logging into FPL on a user's behalf.
- Storing FPL passwords, session cookies, or access tokens.
- Multi-gameweek optimization, chip optimization, price speculation, scheduling, or notifications.
- Using user/population data for online learning, personalization, or unreviewed feature flags.
- Combining production and research storage or credentials.
- Treating historical backfills as prospective evidence.
- Replacing immutable artifacts with mutable database rows.
- Exposing filesystem paths, raw evidence objects, or sealed holdouts to clients.
- Building a broad dashboard, social product, league product, or public recommendation feed in Tasks026B–D.

## 30. Review checklist

Independent review should reject an implementation plan that cannot answer “yes” to each item:

- Does every trusted web view start from an explicit semantic ID and revalidate its artifact chain?
- Is every recommendation value sourced from exactly one verified artifact payload?
- Can neither browser nor API reconstruct optimizer/model/legality output?
- Is manager evidence fresh, user-bound, preparation-bound, and checked with server time?
- Are user identity and public FPL entry identity distinct?
- Can research storage be unavailable without affecting production decisions?
- Does population evidence require explicit consent and a preregistered release gate?
- Are caches keyed by verified content identity rather than `latest`?
- Are personal-data export/deletion paths designed before broad collection begins?
- Can the one-user deployment move to object storage, PostgreSQL, workers, and replicas without changing semantic IDs or artifact contracts?
