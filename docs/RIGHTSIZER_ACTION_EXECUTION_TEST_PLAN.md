# Rightsizer Action Execution — Implementation & Test Plan

**Status:** Plan only. No code changes made.
**Date:** 2026-08-02
**Scope:** EC2, RDS, ElastiCache rightsizers. **ASG explicitly excluded.**
**Decisions taken:** Full flow (build missing wiring + tests). Offline hand-authored payload fixtures only — no AWS calls in any test, no payload capture runs, no `tests_generator` usage.

---

## 1. Current state (verified)

| | EC2 | RDS | ElastiCache |
|---|---|---|---|
| Recommendation API | `GET /rightsizer/resources/ec2/{id}` | `GET /recommendations/rds/rightsize[/{id}]` | `GET /recommendations/elasticache/rightsize[/{id}]` |
| Recommendation kinds | instance-type change | `DB_INSTANCE_CLASS_CHANGE`, `STORAGE_CONFIGURATION_CHANGE` | `NODE_TYPE_CHANGE`, `REPLICA_COUNT_REDUCTION` |
| Action handler | `rightsize` → `handle_ec2_rightsize` (`handlers_ec2.py:373`): stop → waiter → `modify_instance_attribute` → start | **Gap.** Only `rds_migrate_graviton`, which rejects non-Graviton targets (`handlers_rds.py:50`). No storage-change action at all | `elasticache_downsize` (`handlers_elasticache.py:103`): node type + `num_cache_nodes` + `replicas_per_node_group`, both replication-group and cache-cluster paths |
| UI Apply | **Wired** — `Rightsizer.tsx` → `rightsizerApi.applyRecommendation(...)` → `POST /rightsizer/resources/ec2/{inventory_id}/apply` | **Gap** — `RdsRightsizerPanel.tsx` is display-only | **Gap** — `ElasticacheRightsizerPanel.tsx:175` states "Runbook actions are separate" |
| Offline action fixtures | **Gap** — `tests/payloads/ec2/actions/` has stop/terminate/EIP only; no `rightsize` | `rds_migrate_graviton`, `stop` | `elasticache_downsize` (cache-cluster path only; scenario begins with `describe_replication_groups` → error → `describe_cache_clusters` → `modify_cache_cluster`) |
| Execution route | `POST /checks/{check_id}/actions` (`routes/checks.py:912`) — requires a registered check_id; creates `ActionExecution` row; 409 guard on concurrent same-resource actions | same route | same route |

**Test harness that exists and should be reused:** `tests/test_payload_actions.py` + `payload_helpers.build_action_payload_adapter()` → `StrictActionReplay` (asserts exact boto3 operation sequence + kwargs) + `StaticActionSession` + `_NoOpWaiter` (waiters are no-ops, so `get_waiter("instance_stopped").wait()` replays fine). Fixtures are directories:

```
tests/payloads/<svc>/actions/<action_key>/<scenario>/
  capture_metadata.json      # account_id, region, resource_id, parameters, check_id,
                             # calls[] (operation, kwargs, response_file, raises_client_error),
                             # expected_status, expected_message, handler_response
  01_<svc>__<operation>__response.json
  02_...
```

**Harness caveat found:** `load_action_manifest()` reads `tests_generator/<svc>/actions.json` (`payload_helpers.py:51`). The OSS extraction spec excludes `tests_generator/` from the public repo, so **new tests must not parametrize from those manifests**. The new test module carries its own case table (see §5).

---

## 2. Design decisions to make before coding

### D1. How do rightsizer applies reach the action route? — needed for RDS/ElastiCache

The general route is `POST /checks/{check_id}/actions` and 404s unless
`check_id` is in the check registry. EC2 rightsizer applies now use the
dedicated `POST /rightsizer/resources/ec2/{inventory_id}/apply` route, while
RDS/ElastiCache have no dedicated rightsize apply route yet.

- **Option A (recommended, low risk):** register minimal `rds_rightsize_candidate` and `elasticache_rightsize_candidate` checks whose check_function surfaces current rightsizer-recommendation resources (or returns `[]` — the action path only needs registry presence). Follows the EC2 precedent exactly; zero route changes; UI calls stay symmetrical across the three services.
- **Option B (cleaner, more work):** new route `POST /rightsizer/resources/{resource_type}/{inventory_id}/actions` that resolves resource/account/region server-side from inventory and reuses `action_registry`. Decouples rightsizer from the checks concept, and lets the server enforce D3 naturally. Defer unless A feels too hacky in review.

### D2. Parameter contract per recommendation kind

| Recommendation | Action key | Parameters (from the selected tier/candidate) |
|---|---|---|
| EC2 instance-type change | `rightsize` | `target_instance_type` |
| RDS `DB_INSTANCE_CLASS_CHANGE` | **new** `rds_rightsize` | `target_db_instance_class`, `apply_immediately` (default `false` → maintenance window) |
| RDS `STORAGE_CONFIGURATION_CHANGE` | **new** `rds_modify_storage` | `target_storage_type`, `target_allocated_storage_gib`, `target_iops?`, `target_throughput_mibps?`, `apply_immediately` |
| ElastiCache `NODE_TYPE_CHANGE` | `elasticache_downsize` | `target_node_type` |
| ElastiCache `REPLICA_COUNT_REDUCTION` | `elasticache_downsize` | `target_node_type` (current type), `replicas_per_node_group` |

### D3. Server-side classification guard (new behavior)

Today nothing stops an apply for a `CONDITIONAL`/`DEFERRED` recommendation — the guard exists only as UI affordance. Add a server-side check in the apply path: reject unless the current recommendation for that inventory row is `ACTIONABLE` **and** the requested target matches a recommended candidate, with an explicit `override: true` parameter (audited into `ActionExecution.parameters_json`) to bypass. This is the single most valuable safety behavior to test.

### D4. RDS handler semantics

`rds_rightsize` mirrors `rds_migrate_graviton` minus the Graviton restriction, plus: validate target class differs from current (409), instance exists (404), engine/class orderability optional v1-skip (the rightsizer already filtered by orderable options). `rds_modify_storage` calls `modify_db_instance` with storage kwargs only; never combines class + storage in one call (independent failure domains, matches how the recommendations are separated).

---

## 3. Work items

### Phase 1 — EC2 (wiring exists; tests only)

1. **Fixtures** `tests/payloads/ec2/actions/rightsize/`:
   - `success/`: `stop_instances` → `modify_instance_attribute` → `start_instances` (waiter is a no-op in replay). Model response bodies on the existing `stop` capture; placeholder IDs only (`i-PLACEHOLDER-INSTANCE`, account `123456789012`).
   - `invalid_target/`: handler rejects missing/blank `target_instance_type` → 400, no AWS calls.
   - `stop_fails/`: `stop_instances` with `raises_client_error: true` (e.g. `IncorrectInstanceState`) → 500 surfaced, `ActionExecution` marked failed, **no subsequent calls** (StrictActionReplay proves modify/start never happened — the critical invariant).
2. **Handler tests** in new `tests/test_rightsizer_actions.py` (see §5).
3. **Mapping test (frontend)**: vitest test for `Rightsizer.tsx` apply mutation — selected tier's `target_type` and `inventory_id` land in `rightsizerApi.applyRecommendation(...)`; the backend supplies `action === 'rightsize'`; error path renders `applyMessage`. Follows the existing `ElasticacheRightsizerPanel.test.tsx` pattern.

### Phase 2 — ElastiCache (handler exists; wire UI + tests)

1. **Backend**: D1 registration (`elasticache_rightsize_candidate`), D3 guard.
2. **Fixtures** `tests/payloads/elasticache/actions/elasticache_downsize/`:
   - `replication_group_success/`: `describe_replication_groups` → `modify_replication_group` (node-type change path; the existing capture only covers cache-cluster).
   - `replica_reduction_success/`: `describe_replication_groups` → `modify_replication_group` with `ReplicasPerNodeGroup`.
   - `node_count_exceeds_current/`: describe → 400, no modify call.
3. **UI**: Apply button on `ElasticacheRightsizerPanel` for the selected tier + replica recommendation, mirroring the EC2 mutation; disabled unless classification `ACTIONABLE` (CONDITIONAL requires an explicit confirm step, matching D3 override). Remove/replace the "Runbook actions are separate" copy.
4. **Component tests**: apply calls `executeAction` with `target_node_type` from the selected tier; replica apply sends `replicas_per_node_group`; CONDITIONAL requires confirm; error state renders.

### Phase 3 — RDS (build handler + wire UI + tests)

1. **Backend**: new `handle_rds_rightsize` + `handle_rds_modify_storage` in `handlers_rds.py` per D4; register both; D1 registration (`rds_rightsize_candidate`); D3 guard.
2. **Fixtures**:
   - `rds_rightsize/success/`: `describe_db_instances` → `modify_db_instance` (class change, `ApplyImmediately=false`).
   - `rds_rightsize/same_class/`: describe shows target == current → 409, no modify.
   - `rds_rightsize/not_found/`: describe returns empty → 404, no modify.
   - `rds_modify_storage/success/`: describe → `modify_db_instance` with `StorageType`/`AllocatedStorage`/`Iops`/`StorageThroughput`.
   - `rds_modify_storage/downsize_rejected/`: target allocated < current → 400, no modify (AWS forbids storage shrink; handler must pre-validate).
3. **UI**: Apply on `RdsRightsizerPanel` for the selected class tier and (separately) the storage recommendation. Same classification gating as Phase 2.
4. **Component tests**: class apply → `target_db_instance_class` from selected tier; storage apply → storage params from `target_storage`; the two applies never combine.

### Phase 4 — Route-level and guard tests (shared)

FastAPI `TestClient` against `POST /checks/{check_id}/actions` with a monkeypatched `AWSAdapter` (in-memory SQLite via existing test session pattern):

- `ActionExecution` lifecycle: row created `running` → `submitted` on success; `failed` + `error_message` on handler exception.
- 409 concurrent guard: second apply for the same resource while one is `running`.
- 404 unknown check_id; 400 unsupported action for the resource type.
- **D3 guard matrix**: ACTIONABLE + matching target → allowed; CONDITIONAL without override → rejected; CONDITIONAL + `override: true` → allowed and override recorded in `parameters_json`; target not in recommended candidates → rejected.
- Feature-flag: applies rejected when `test_action`/actions flag is off (whatever gate D3 lands on).

---

## 4. Fixture authoring guide (no capture runs)

1. Copy the nearest existing capture as a template (`ec2/actions/stop`, `rds/actions/rds_migrate_graviton`, `elasticache/actions/elasticache_downsize`).
2. Response bodies: trim to the fields the handler actually reads plus `ResponseMetadata.HTTPStatusCode` — consult the handler, not the full AWS response shape.
3. Only placeholder identifiers: account `123456789012`, `i-PLACEHOLDER-*`, `maxops-test-*`. Nothing copied from real captures without re-checking for real IDs (per the OSS extraction spec §3.1).
4. Set `"capture_source": "hand-authored"` in `capture_metadata.json` so future tooling can distinguish these from real captures.
5. Error scenarios use `"raises_client_error": true` with an `__error.json` body containing the AWS error code — see `elasticache_downsize`'s `01_elasticache__describe_replication_groups__error.json` for the format.

## 5. New test module layout

`tests/test_rightsizer_actions.py`, marked `pytestmark = [pytest.mark.unit, pytest.mark.payload]`:

- **Own inline case table** (service, action_key, scenario, expected_status/message/calls) — deliberately *not* `load_action_manifest()`, to avoid the `tests_generator/` coupling (§1 caveat).
- Reuses `build_action_payload_adapter` unchanged.
- One parametrized replay test (mirrors `test_action_manifest_replays_captured_aws_calls`) + targeted tests for the error scenarios asserting both the HTTP status and that `replay` shows no calls after the failing one.
- Recommendation→parameter mapping tests: feed a canned rightsizer response dict into whatever helper builds the apply request (backend side of D2), assert exact parameter payload per recommendation kind.

Frontend: extend the existing `__tests__` directories (`components/elasticache/__tests__/`, `components/rds/__tests__/`, add one for `pages/Rightsizer`), mocking `checksApi.executeAction`.

## 6. Safety rails for executing this plan

- All new tests are offline; none touch AWS. **Do not** run `tests_generator/` or `tests/integration/` to produce fixtures — fixtures are hand-authored per §4.
- Run tests scoped, per CONTRIBUTING.md: `pytest tests/test_rightsizer_actions.py`, `pytest tests/test_payload_actions.py` — never a broad `-k` sweep.
- The D3 guard ships **before or with** the RDS/ElastiCache Apply buttons, never after — the UI must not gain destructive reach ahead of the server-side check.
- Manual verification against a real account (if ever desired) is out of scope for this plan and would be a deliberate, separate exercise.

## 7. Suggested order & sizing

| Step | Contents | Size |
|---|---|---|
| 1 | Phase 1 (EC2 fixtures + tests, frontend mapping test) — no product code changes | S |
| 2 | D1 + D3 backend (guard + check registrations) + Phase 4 route/guard tests | M |
| 3 | Phase 2 ElastiCache (UI apply + fixtures + tests) | M |
| 4 | Phase 3 RDS (two new handlers + UI + fixtures + tests) | L |

Step 1 is pure test-adding and can land immediately; it also proves the replay harness handles the multi-call stop→modify→start sequence before any new product code depends on it.

## 8. Open questions (non-blocking, flag during implementation)

1. D1 Option A vs B — plan assumes A; revisit if the pseudo-check registration feels wrong in review.
2. Should EC2's existing apply also go behind the D3 guard? (Recommended: yes, same matrix — it currently has no server-side classification check either.)
3. `elasticache_downsize` sets `ApplyImmediately: True` unconditionally — expose it as a parameter like the RDS handlers will? (Recommended: yes, default `true` to preserve current behavior.)
