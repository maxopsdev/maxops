# S3 Optimizer — V1 Specification

Status: implementation contract. Authority for the `s3_bucket_*` optimizer checks; `docs/EC2_TELEMETRY.md` §6 supplies the absent-is-not-zero invariant they inherit; `pricing/cur/datasets.py` is the authority for the dataset pattern this feature mirrors.

S3 Optimizer analyzes storage-class rightsizing for S3 general-purpose buckets: given a bucket's current class mix and observed request/retrieval behaviour, it scores candidate lifecycle policies (Standard-IA, Glacier Instant Retrieval, Intelligent-Tiering, Glacier Flexible, Deep Archive) and reports savings, breakeven, and risk per candidate. Chained transition sequences are deferred (§10). V1 ships analysis and advisory findings only — nothing here executes a change.

---

# 1. Scope

## 1.1 V1

Three checks plus the scenario engine that backs them:

| Check ID | Signal | Advisory action |
| --- | --- | --- |
| `s3_bucket_unused` | zero requests over the window, inside CUR coverage | `s3_review_unused_bucket` |
| `s3_bucket_low_access` | very low request rate + low retrieval ratio | `s3_review_storage_class` |
| `s3_bucket_retrieval_cost_dominant` | archive/IA class costs more in retrieval than it saves in storage | `s3_restore_to_standard` |

All three target **general-purpose buckets** only and read a common per-bucket signal set (§3.4) from a shared service, `s3_optimizer.py` — not duplicated per check, so the engine's numbers are the same whichever check or future UI/MCP surface reads them.

Advisory only, matching the SageMaker V1 precedent (`app/checks/sagemaker/sagemaker_checks_spec.md` §6): no V1 handler calls `PutBucketLifecycleConfiguration`, `CopyObject`, or any mutating S3 API.

## 1.2 Out of scope for V1

* **Directory buckets (S3 Express One Zone)** — different classes, different CUR usage types, no lifecycle transitions to the classes covered here.
* **S3 on Outposts** — not billed through the CUR usage types this spec maps.
* **Noncurrent-version splitting** — owned by the existing `app/checks/s3/` lifecycle checks. **Correction**: `storage_class_breakdown` (§3.1) is **not** current-version-only — `BucketSizeBytes` and `NumberOfObjects` include noncurrent versions, delete markers, and incomplete multipart-upload parts, and V1 has no CloudWatch-only way to isolate current versions (§2.4). V1 does not attempt the split; it flags versioned buckets instead (`NONCURRENT_VERSIONS_INCLUDED`, §4.7) and points at the existing lifecycle checks for that accounting.
* **S3 Storage Lens** — would give exact per-prefix, per-class counts and remove the §2.4 estimation, but requires an org-level opt-in not every account has. Tracked in §10.
* **S3 Inventory reports** — same reason: better ground truth, an opt-in dependency V1 cannot assume exists.

---

# 2. Cross-cutting rules

## 2.1 Absent is not zero

Inherited from `docs/EC2_TELEMETRY.md` §6, with an S3-specific coverage test because CUR, not CloudWatch, is the primary signal source:

* A bucket is **inside CUR coverage** for a month only if the cache has at least one `TimedStorage-*` line for that bucket that month — storage is billed hourly for every stored byte, so its presence is the right proxy for "CUR is actually capturing this bucket."
* **Zero request/retrieval lines inside a covered month is a true zero.**
* **Zero storage lines in the whole window** means untracked, not idle: the bucket may postdate the CUR export, predate the cache backfill, or have a missing `line_item_resource_id` (§10). `telemetry_status: "unknown"`, **never a finding** — an unused bucket and an unobserved one must never look the same to someone deciding whether to delete something.
* Every result reports `cur_days_covered`, `cur_first_day`, `cur_last_day`, `cloudwatch_days_covered`.
* **No minimum-window gate.** Confidence substitutes for a hard cutoff:

  | `cur_days_covered` | `confidence` |
  | --- | --- |
  | < 30 | `low` |
  | 30–89 | `medium` |
  | ≥ 90 | `high` |

Findings are never suppressed for low confidence; the label travels with the result so it can be weighted downstream.

## 2.2 Data sources: no billed request metrics, ever

V1 reads only the **free** CloudWatch daily storage metrics: `AWS/S3 BucketSizeBytes` (per `StorageType`) and `AWS/S3 NumberOfObjects` (`StorageType=AllStorageTypes`, the metric's only value for that dimension). Both publish daily at no cost for every bucket already.

S3 **request metrics** (`AllRequests`, `GetRequests`, per-minute latency, etc.) are billed and opt-in. This spec forbids both enabling them and reading them if another team enabled them for an unrelated reason — a cost optimizer that turns on a billed metric would be self-defeating. Every request/retrieval signal instead comes from **CUR** (§3.3), free to read.

## 2.3 Request signal semantics — operation families, not raw tiers

**Correction: CUR request tiers do not map 1:1 onto "write" and "read,"
and — evidenced against a captured account — Tier2 in a real account is
dominated by requests that are not application data access at all.**
`Requests-Tier1` bills PUT/COPY/POST **and LIST**, priced at each class's
Tier1 rate — but AWS always [prices a LIST request at the **Standard**
PUT/COPY/POST/LIST rate regardless of the object's class](https://aws.amazon.com/s3/pricing/).
Worse: a captured CUR sample across the account's buckets showed Tier2
traffic dominated by **bucket-configuration reads** —
`ReadBucketLifecycle` (13,439 requests) and `ReadLocation` (8,485
requests) across ~93 buckets — versus `GetObject` (492 requests, on only
8 buckets). Tier1 similarly carried `CreateBucket`, `WriteBucketLifecycle`,
`WriteLogProps`, and `ListBucketVersions`. **This is not incidental: any
scanner that inventories S3 buckets — including MaxOps's own scans —
issues `ReadBucketLifecycle`/`ReadLocation`/similar config calls against
every bucket it touches**, so a naive Tier1/Tier2 split would make every
scanned bucket look "used" regardless of whether any application ever
reads its objects. Generic `Requests-Tier3` mixes lifecycle transitions
with Glacier Flexible restore requests on the same usage type, and
`Requests-Tier4` covers several destination classes without naming which
one — both resolved by `line_item_operation`, per §3.3's registry.

V1 therefore aggregates by **operation family**, derived from
`line_item_operation`:

| Family | Operations | Priced at |
| --- | --- | --- |
| `data_read` | `GetObject`, `HeadObject`, `SelectObjectContent`, `GetObjectTagging`, `GetObjectAttributes` | target class's Tier2 rate |
| `data_write` | `PutObject`, `CopyObject`, `PostObject`, `UploadPart`, `CompleteMultipartUpload`, `InitiateMultipartUpload` | target class's Tier1 rate |
| `list` | `ListBucket`, `ListBucketVersions`, `ListMultipartUploads` | **Standard** Tier1 rate, always — never the target class's rate |
| `restore` | `RestoreObject` | the class's restore-request rate, **not** a transition cost |
| `transition` | `S3-*Transition` (`S3-SIATransition`, `S3-ZIATransition`, `S3-GIRTransition`, `S3-INTTransition`, `S3-GlacierTransition`, `S3-GDATransition`) | target class's Tier3/Tier4 rate (§2.4) |
| `config` | everything else: `Read*`/`Write*` bucket-configuration calls, `HeadBucket`, `CreateBucket`, `GetBucket*`, `PutBucket*`, `ListObjectAnnotations` | still **priced**, at Standard Tier1/Tier2 rates by request shape, but **never counted as access** (§4.9's checks) |

`config` exists specifically because of the evidence above: it is priced
(these are still billable requests) but excluded from every access
signal — `s3_bucket_unused`, `s3_bucket_low_access`, the pattern
classifier, and `requests_per_object_per_month` all read only
`data_read`/`data_write`/`list`, never `config` (§4.9's checks, §3.5,
§5.1/§5.2). `signals` and `window_totals` (§3.4, Appendix) carry a
separate `config_requests` count so it is visible, not hidden — a bucket
that is genuinely unused should still show the config-scan traffic that
touched it, just not have that traffic count as evidence of use.

**When `line_item_operation` names the operation**, classify it into the
table above and record `operation_source: "line_item_operation"`. **`tier1_unsplit`/`tier2_unsplit` apply only when `line_item_operation` is
empty** (a usage-type-only line, seen on some CUR exports) — a generic
Tier1/Tier2 row with no operation cannot be split into `data_write`/`list`/
`config` (Tier1) or `data_read`/`config` (Tier2); it is priced at the
**target class's** Tier1/Tier2 rate (never the cheaper Standard-LIST
rate) — deliberately the conservative (higher) choice, since guessing
"mostly LIST/config" could understate a real write/read-heavy cost.
Record `operation_source: "usage_type_tier"` for this fallback, and
**exclude `tier1_unsplit`/`tier2_unsplit` from access signals too** —
without an operation, V1 cannot tell config from data, so the
conservative choice for *pricing* (assume data-class rate) is paired with
the conservative choice for *access* (assume it might be config, don't
count it). A generic Tier3/Tier4 row with no `line_item_operation` is
**not** assigned an invented destination class or family at all — it
lands in category `"transition_or_restore_ambiguous"`/`"transition_generic"`
with `storage_class: None` (§3.3), because guessing "transition" when it
might be a restore (or vice versa) would misprice the wrong line item.

**UI/report text for the `tier1_unsplit` fallback must still say
"PUT/COPY/POST/LIST"**, never "writes" — the ambiguity is exactly why that
rule exists. For the disambiguated `data_read` family, UI/report text
says **"GET/HEAD/other billed data requests,"** not "reads" — `SELECT`
and similar operations are not reads in the everyday sense but bill on
the same family; `config` requests are reported as "bucket configuration
requests," never folded into either "reads" or "writes."

## 2.4 Per-class object count derivation

`NumberOfObjects` is a single bucket-wide aggregate, not per class.

**Correction (all counts in this section are population-wide, not
current-version-only).** Both `BucketSizeBytes` and `NumberOfObjects`
[include current objects, noncurrent versions, delete markers, and
incomplete multipart-upload parts](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html)
— there is no `StorageType` or dimension that isolates current versions.
Every count and average-size figure this spec derives from these two
metrics is therefore a population count over that mixed set, not a
current-version object count. This means `object_count_method` values
other than `overhead_derived` are **never exact** even in the
single-nonzero-class case, because a versioned or MPU-heavy bucket can
still have current-version and total-population counts that differ. V1
does not attempt to separate them (§1.2); it flags the ambiguity instead
via `NONCURRENT_VERSIONS_INCLUDED` (§4.7). §10 item 2 (S3 Inventory) is
the actual path to exact, current-version-only counts — Inventory reports
carry version status per row, which neither of these two metrics does.

* **Archive classes carry a fixed 32 KB per-object index overhead**, exposed as separate **CloudWatch `BucketSizeBytes` `StorageType` dimension values** — `GlacierObjectOverhead`, `DeepArchiveObjectOverhead`, `IntAAObjectOverhead`, `IntDAAObjectOverhead` — **not** a CUR usage type. Since the overhead is fixed size, dividing that `StorageType`'s bytes (from CloudWatch, §3.2) by 32768 gives an exact **population** count: `objects_glacier = GlacierObjectOverhead_bytes / 32768` (same formula for `objects_deep_archive`, `objects_int_aa`, `objects_int_daa`). `object_count_method: "overhead_derived"` — this one is exact in the population sense (every object charged that overhead, current or not), just not current-version-isolated. No CloudWatch datapoint for that `StorageType` that month → the class's count is `None`, never `0` (absence does not prove zero objects). Separately, the `StandardIASizeOverhead`, `OneZoneIASizeOverhead`, and `GlacierIRSizeOverhead` `StorageType` values give the **observed** small-object padding bytes (the §2.6 128 KB floor already applied by AWS) for objects already stored in those classes — used directly in §4.1 when the bucket already holds bytes there, rather than estimated.
* **Remaining classes** (Standard, SIA, ZIA, GIR, INT Frequent/Infrequent) have no fixed signal. Estimate by **byte share**: `total_objects − archive_objects`, distributed proportional to each class's share of non-archive bytes. `object_count_method: "byte_share_estimate"` — assumes similar average object sizes across classes; callers must not treat it as exact.
* **Renamed from "exact" to `"remainder_derived"`**: exactly one non-archive class holds nonzero bytes → its count is `total_objects − archive_objects` directly, with no distribution math needed. This is **not** relabeled `"exact"` because — per the correction above — `total_objects` itself may include noncurrent versions/delete markers/MPU parts the bucket's actual current-version count does not have; the arithmetic is exact given the (population) inputs, but the inputs are not what "current object count" means.
* **Average object size** = `bytes / objects`; `None` whenever `objects` is `0` or `None` (never a divide-by-zero, never unknown-treated-as-zero). Same population caveat applies.

## 2.5 Pricing: a CUR-derived price map, no Pricing API calls in V1

**Design change**: V1 does not call the AWS Pricing API at all. Every
price this feature needs is either **observed directly in the same CUR
data already being read** (§3.3) — `unblended_cost / usage_amount` on the
account's own billed rows *is* the price, with no separate API call or
filter-matching risk — or, for keys CUR has not billed in the current
window, filled from a small hand-maintained **seed table**. This replaces
the earlier Pricing-API-based `S3PriceCatalog` design entirely; the
GetProducts filter-matching concerns that design existed to manage (first
match vs. exact match, `group`/`groupDescription` drift, per-request unit
normalisation) do not apply when the price comes from a cost the account
was actually billed.

**Primary source — CUR-observed rates.** For each `(region,
usage_type_suffix, line_item_operation)` triple observed in the pricing
window, `s3_bucket_source.py` computes:

```text
unit_price = SUM(unblended_cost) / SUM(usage_amount)
             over rows where line_item_line_item_type = 'Usage'
               AND unblended_cost > 0
               AND usage_amount ≥ minimum_usage_amount (param, default 0.001, in the row's own pricing_unit)
```

**`unblended_cost`, never `net_amortized_cost`** — credits, Savings
Plans, and other discounts would understate the rate a candidate class
will actually charge going forward; the savings comparison needs the
sticker price, not this account's negotiated/discounted one. **Zero-cost
rows (free tier) are excluded, never averaged in as a `$0` price** — a
free-tier row diluting the observed rate toward zero would understate
every subsequent request/storage cost for as long as free tier applies.
Each observed `(usage_type_suffix, operation)` pair maps to the same
canonical keys the engine already uses: `storage.<class>.gb_month`,
`request.<class>.<family>.per_1000`, `transition.<class>.per_1000`,
`retrieval.<class>.<speed>.gb`, `restore_request.<class>.<speed>.per_1000`,
`it_monitoring.per_1000_objects`, `early_delete.<class>.gb` — the
per-1000 keys are derived as `cost / (usage_amount / 1000)`.

**Worked example, from the reference capture bucket (`bucket-a` in the scrubbed fixture) (§8.2)**:

```text
StandardStorage:      0.0239 GB-Mo billed at $0.00055  → $0.023 /GB-month
S3-SIATransition:     100 requests billed at $0.001     → $0.01 /1000
S3-GIRTransition:     100 requests billed at $0.002      → $0.02 /1000
S3-GlacierTransition: 100 requests billed at $0.003       → $0.03 /1000
S3-GDATransition:     100 requests billed at $0.005        → $0.05 /1000
Retrieval-GIR:         0.1058 GB billed at $0.00317        → $0.03 /GB
```

**Fallback source — release street-pricing table.** The `street_pricing_s3`
table in `maxops_pricing.db`, keyed by `(region, canonical_key)`, is generated
for the 15 `TARGET_REGIONS` by `staging_pricing/import_s3_pricing.py` from the
AWS Pricing API at release time; runtime still makes no Pricing API calls.
The JSON seed remains a us-east-1 bootstrap fallback and is used **only** for keys
the CUR window has not observed billed usage for — e.g. `DEEP_ARCHIVE`
storage, early-delete fees, or Glacier retrieval bytes in an account that
has not exercised them yet (§3.3/§10's unverified-registry items are
exactly this gap). A unit test asserts every canonical key the engine can
request exists in the seed for at least `us-east-1`, so a missing seed
entry is a test failure, not a silent `pricing_unavailable` at runtime for
a key that should always be resolvable.

The release importer queries both `AmazonS3` and
`AmazonS3GlacierDeepArchive`: standalone Deep Archive storage, PUT, restore
request, and early-delete products use the latter service code. The verified
selectors never filter on `productFamily`, because 110 of the 179
`AmazonS3` products in the capture have no `productFamily`; they match exact
usage type after region-prefix stripping plus the table-specified operation,
group, and fee code.
Keys verified as not sold are retained in `street_pricing_s3` with
`price_usd: NULL` and `status: "not_applicable"`; the runtime excludes those
rows and reports `pricing_unavailable`, never a zero price.

**Every resolved key records `price_source: "cur_observed"`,
`"street_pricing"`, or `"seed"`** so a consumer can tell whether a number came from this
account's own billing or a hand-maintained fallback. Unresolved keys
(observed in neither CUR nor the seed table) keep the existing
`status: "pricing_unavailable"` behaviour (§2.5, below) — unchanged from
the Pricing-API design. The result additionally carries
`price_map_window: {start, end}` (the CUR window the observed rates were
computed over) and `seed_as_of` (the seed table's own `as_of` date, for
whichever entries it contributed).

**Tiered pricing** — unaffected by this source change: the *observed*
rate is simply whatever tier the account's actual usage falls in (no
separate tier-selection logic needed for CUR-observed prices), and the
seed table uses the first (lowest-volume) tier, recorded as assumption
`pricing_tier: "first_tier"`, for any key it fills. V1 still does not
model tier position or consolidated billing — applying the seed's
first-tier rate to a bucket's *current* Standard storage can overstate
the baseline cost the savings comparison is measured against (in
us-east-1, roughly $0.023 vs. $0.021/GB for a bucket large enough to
reach the next tier), which inflates rather than deflates claimed
savings. §10 keeps the tier-aware-pricing item.

**No database session required for pricing.** `app/pricing/s3_price_map.py`
**replaces** `s3_price_catalog.py` — its `resolve(rows, region,
window) -> {"resolved": {key: {price, price_source}}, "unresolved":
[key]}` takes the CUR rows already read for the bucket (or region) and
the seed table (loaded once, not per call); it needs no `Session` at all,
since there is no cache table to read or write and no external API call
to make. **This simplifies §2.5's dependency-path shape**: `scan_service`
still computes the price map **once per region** (recomputing it once,
not once per bucket, is still worth doing even though no session is
needed, since the underlying CUR query is shared) and the `s3_optimizer`
result **once per bucket**, storing both into
`S3Inventory.metadata_json["s3_optimizer"]` as before (§2.5, below) — but
the reason a DB session is present in that pipeline step is now only the
`S3Inventory` write itself, not pricing.

**Recomputed every scan, never persisted as a standalone value.** The
price map is derived fresh from the CUR window on every scan run (one
DuckDB query in `s3_bucket_source.py`, alongside the bucket-signal
queries §3.3 already runs) — it is not cached across scans as its own
artifact, only as part of each bucket's frozen `s3_optimizer` result.
`price_catalog_version` (§2.5, below) becomes the pair `(price_map_window,
seed_as_of)` — the two facts that together determine what any given
resolved price actually was, replacing the earlier Pricing-API-based
version stamp.

**Correction — the metadata bridge to checks.** An earlier draft assumed
this stored value reaches the three checks via `aws_adapter.get_resources(...)`,
the same call `app/checks/s3/no_expiration_set.py` and its siblings use.
That assumption is wrong: `CachedAWSAdapter.get_resources`
(`app/services/scan_service.py`) returns `copy.deepcopy(self._cache[key])`
— a deep copy of the **original AWS payload** the adapter fetched, never
the persisted `S3Inventory.metadata_json`. `_enrich_ec2_rightsizing_metrics`
does not bridge this gap either: it mutates `resource["metadata"]` on the
**inventory phase's own local resource list**, which is what gets
persisted to the database, but the **policies phase** (where checks run)
calls `check_registry.execute_check`, and a check's own
`aws_adapter.get_resources(...)` call goes through the same
`CachedAWSAdapter` deep-copy path, never seeing that inventory-phase
mutation. There is no existing mechanism that gets enrichment metadata
into what a check's `get_resources` call returns.

V1 adds one: `CachedAWSAdapter.overlay_resource_metadata(resource_type:
str, resource_id: str, key: str, value: Any)` — finds the matching
resource(s) for `resource_type`/`resource_id` across the adapter's cached
`get_resources` entries (`self._cache`, keyed by `(resource_type, filters,
region)`) and sets `metadata[key] = value` **on the cached payload
itself**, not on a returned copy, so every subsequent `get_resources` call
against that same `CachedAWSAdapter` instance (deep-copying from the
now-updated cache) includes it. `scan_service` calls this immediately
after computing each bucket's `s3_optimizer` result — in the same S3
enrichment step that computes the price map (§2.5) and runs the scenario
engine, using the **same `CachedAWSAdapter` instance** (`_get_cached_adapter`,
keyed per region in `adapter_cache`) that the policies phase later reuses
for that region — and **before** the policies phase runs the three S3
Optimizer checks. The three checks then read
`resource["metadata"]["s3_optimizer"]` off their own
`aws_adapter.get_resources(...)` call, exactly as before, but now that
call actually returns the overlay. §9 names the exact `scan_service`
entry points. The API endpoint and MCP tool (§7) read the same stored
result directly from `S3Inventory.metadata_json` (not through the
adapter) — the stored scan result is served as-is, with no on-demand
recompute path (§10 — a recompute flag was considered and cut from V1:
the stored scan result is enough, and recomputing on request would
duplicate the scan pipeline's own orchestration).

Implementation correction: the overlay store is scan-scoped and shared across all regional `CachedAWSAdapter` instances, so a global S3 listing receives enrichment regardless of which regional adapter returns the bucket.

`app/pricing/pricing_s3.py`'s three hardcoded constants
(`S3_STANDARD_PRICE_PER_GB = 0.023`, `S3_GLACIER_PRICE_PER_GB = 0.004`,
`S3_DEEP_ARCHIVE_PRICE_PER_GB = 0.00099`) are **not removed outright**:
`estimate_s3_storage_cost` (called sessionlessly today by `scan_service.py`
during S3 inventory enrichment, before `PricingContext`/`handle_s3_pricing`
exist for that resource) **keeps its current sessionless signature** and
gains an optional `price_map` parameter. When `price_map` is absent, it
behaves exactly as today — the three constants survive **only as that
fallback path**, marked deprecated, until the `scan_service` caller is
updated to pass the §2.5 price map's `resolved` dict into it.
`handle_s3_pricing` (which already receives a session through
`PricingContext`, used only for its `S3Inventory` write, not for pricing)
is reworked onto `app/pricing/s3_price_map.py` directly (§9), not onto
`AwsPricingCacheService`.

**Unknown price → that scenario is `status: "pricing_unavailable"`, never priced at `0`.** The `scan_service` enrichment step reads the price map's `unresolved` list (§2.5) so the gap is visible, not silently substituted.

## 2.6 Minimum billable sizes and durations

| Class | Min billable object size | Min storage duration |
| --- | --- | --- |
| Standard-IA / One Zone-IA | 128 KB | 30 days |
| Glacier Instant Retrieval | 128 KB | 90 days |
| Glacier Flexible | — | 90 days |
| Deep Archive | — | 180 days |
| Intelligent-Tiering | 128 KB (objects below never tier out of Frequent Access and are not monitored — no fee) | 30 days to Infrequent-Access eligibility; 90 days (automatic) to Archive Instant Access; opt-in Archive Access / Deep Archive Access use operator-configured day thresholds, §4.6 |

Glacier Flexible and Deep Archive additionally carry the 40 KB per-object overhead from §2.4 (32 KB archive-rate + 8 KB standard-rate), independent of the size floor. Objects smaller than a class's floor are billed as the floor size. V1 has no per-object size list (§4.1's padding formula), so this is applied either from the observed CloudWatch `*SizeOverhead` metric (§2.4, when the bucket already holds bytes in the class) or estimated from the bucket's average object size (when evaluating a candidate the bucket isn't in yet) — a skewed size distribution can hide a large population of small objects behind an average that looks fine; see §10 item 2 for the exact-count fix.

Leaving a class before its minimum duration triggers an **early-delete charge** = remaining days in the period × the class's per-GB rate. V1 does not project this for *future* hypothetical transitions (requires assuming operator timing); it only reports `EARLY_DELETE_OBSERVED` when CUR shows the account already paid one for this bucket in the window.

Intelligent-Tiering has no retrieval fee and no early-delete fee — its downside is the monitoring fee and `IT_FULL_READ_PROMOTES_TO_FA` (§4.7): a full read moves touched objects back to Frequent Access for 30 days, no retrieval fee but that month's tiering saving is erased.

**Lifecycle eligibility floor (new, since September 2024) and the
unavailable size distribution.** By default, [AWS lifecycle transitions
skip any object under 128 KB](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html)
unless the rule sets an explicit `ObjectSizeGreaterThan` /
`ObjectSizeLessThan` filter that overrides it. **Correction**: an earlier
draft of this section tried to compute `stored_gb(objects≥128KB)` and
`stored_gb(objects<128KB)` separately to apply this floor — but V1 has
only the bucket's **average** object size (§2.4), never a distribution,
and an average alone cannot say how many bytes or objects fall on either
side of 128 KB. A bucket averaging 200 KB could be "all objects 200 KB"
or "half at 10 KB, half at 390 KB" — the floor applies completely
differently to each. V1 does not invent that split. Instead, every
transition-based scenario is priced under one of exactly two rules,
selected by `avg_object_bytes` alone (never estimated further):

* **`avg_object_bytes ≥ 131072`**: assume **every** object is eligible —
  the whole population transitions, the whole population pays padding
  and overhead where applicable. This is deliberately the **upper bound**
  on transition cost (a real population with the same average but some
  objects below 128 KB would have *fewer* eligible objects and a
  *smaller* real transition fee), so a scenario computed this way can
  overstate cost but never understate it. Assumption field
  `size_distribution: "unknown_assumed_all_eligible"` records this
  choice explicitly in every such scenario.
* **`avg_object_bytes < 131072`**: the scenario's `status` is
  **`"size_distribution_unavailable"`** — V1 does not price a transition
  at all for this bucket, because an average below the floor means a
  materially-sized population is plausibly *below* it too, and neither
  the eligible count nor the ineligible count is known. `reason`: "default
  lifecycle rules skip objects under 128 KB; the population's split above
  and below that line is unknown from average size alone."

Every transition-based scenario also carries
`lifecycle_small_object_override: false` by default, and this spec's
proposed lifecycle rules (in advisory text, §6) state
`ObjectSizeGreaterThan=131071` explicitly so the produced rule matches
what the scenario priced. A rule created **before** September 2024 may
still retain the legacy (transition-everything) behaviour if it has never
been re-saved — the advisory text notes this as a caveat when
recommending a change to an *existing* rule. **A scenario variant that
assumes the override** (`lifecycle_small_object_override: true`) is only
computed when the operator explicitly sets that param, and under the
override every object transitions regardless of size (this is the one
case where `avg_object_bytes < 131072` still produces a priced,
non-`size_distribution_unavailable` scenario — the override removes the
ambiguity the default-rule case can't resolve, because *every* object is
now known to be eligible, not just the ones above 128 KB).
`TRANSITION_COST_DOMINATES` (§4.7) is therefore only illustrated for the
override case in this spec's worked example (§4.8) — under the default
rule, a bucket with `avg_object_bytes < 131072` never reaches a priced
transition scenario to dominate in the first place.

**`DIRECT_WRITE_<CLASS>` (§4.6) and the "no change" outcome are unaffected
by any of this** — writing with the `x-amz-storage-class` header is not a
lifecycle transition, so AWS applies no size-based skip and
`DIRECT_WRITE`'s padding cost applies to objects of every size; "no
change" needs no size distribution at all, since it prices nothing.

---

# 3. Discovery, inventory, sources

## 3.1 Inventory additions

`S3Inventory.metadata_json` (`app/models/inventory.py`) gains keys, additive only — persisted JSON never has existing keys renamed or removed without a migration:

* `storage_class_breakdown`: `{class: {bytes, objects, object_count_method, avg_object_bytes}}` — §2.4 output.
* `lifecycle_transitions`: **Correction** — the current adapter discovery
  (`app/adapters/aws/adapter.py`'s S3 lifecycle fetch) wraps
  `get_bucket_lifecycle_configuration` in a bare `except:` that maps
  **every** exception, including `AccessDenied`, to `[]` — collapsing "no
  lifecycle configuration exists" and "we don't know, the call failed"
  into the same value, which is exactly the absent-is-not-zero violation
  §2.1 forbids. That existing method is used today by eight checks —
  `app/checks/s3/no_expiration_set.py`, `no_archival_policies.py`,
  `no_noncurrent_expiration.py`, `no_life_cycle_policies.py`,
  `no_delete_marker_expiration.py`, `logs_bucket_no_expiration.py`,
  `no_mpu_policy.py`, `no_noncurrent_objects_archival.py` — all of which
  look up the method by one of several tolerated names (`_get_bucket_lifecycle`'s
  `getattr` probe in `no_expiration_set.py`: `get_s3_bucket_lifecycle`,
  `get_bucket_lifecycle`, `get_bucket_lifecycle_configuration`,
  `get_resource_lifecycle`) and expect a **raw** `{"Rules": [...]}`-shaped
  dict back, not a typed result. **V1 does not change that method's
  signature or behavior** — breaking it would break eight existing checks
  for one new feature's benefit. Instead, V1 adds a **new**, additional
  adapter method, `get_s3_bucket_lifecycle_typed`, used **only** by the S3
  Optimizer, returning `{status: "usable" | "access_denied" | "error" |
  "absent", rules: [...], error: str | None}` — only
  `NoSuchLifecycleConfiguration` maps to `status: "absent"`; every other
  exception (`AccessDenied`, throttling, a malformed response) maps to
  `"access_denied"` or `"error"` with the message preserved.
  `S3Inventory.metadata_json.lifecycle_transitions` is populated from this
  new method: the parsed rules — `{rule_id, status, prefix_filter,
  tag_filter, transitions: [{to_class, days}]}` — only when `status ==
  "usable"` and rules exist; `[]` only when `status == "absent"` (a real,
  confirmed absence); and **`None`** for `"access_denied"`/`"error"`
  (unknown, never treated as "no rule exists"). Both methods call the same
  underlying `get_bucket_lifecycle_configuration` boto3 call; only the
  wrapping/error-handling differs, so there is no duplicated AWS call
  logic, only duplicated (and intentionally different) exception handling.
* `intelligent_tiering_config`: **not** a `"present"|"absent"` flag — a
  parsed list, `[{id, status, prefix, tags, tierings: [{access_tier,
  days}]}]`. The adapter needs a **new, paginated**
  `list_bucket_intelligent_tiering_configurations` collection method
  (`ListBucketIntelligentTieringConfigurations` has no current adapter
  coverage at all — this call does not exist in
  `app/adapters/aws/adapter.py` today, so there is no existing consumer to
  preserve, unlike the lifecycle case above) returning the same
  typed-result shape: `{status: "usable" | "access_denied" | "error",
  configurations: [...], error: str | None}` — there is no AWS "does not
  exist" exception for this API the way `NoSuchLifecycleConfiguration`
  exists for lifecycle, so `status` here is only
  `usable`/`access_denied`/`error`, and an empty `configurations` list
  under `usable` means a confirmed zero, not an absence. **This is a
  single new method used only by the optimizer**, following the same
  typed-result shape as `get_s3_bucket_lifecycle_typed` above for
  consistency, not because anything else needs it. `None` on
  `intelligent_tiering_config` when the call could not be made (access
  denied/error), `[]` when the call succeeded and returned no
  configuration. `tierings[].access_tier` is one of AWS's own values
  (`ARCHIVE_ACCESS`, `DEEP_ARCHIVE_ACCESS` — the two *opt-in* tiers;
  Frequent/Infrequent/Archive-Instant-Access are automatic and never
  appear here because they are not separately configured, §4.6) with the
  operator's configured `days` threshold for that tier. **No V1 scenario
  consumes `ARCHIVE_ACCESS`/`DEEP_ARCHIVE_ACCESS` entries** — the
  archive-tiers-configured candidate that would have used them is out of
  V1 (§4.6, §10); this discovery is retained as groundwork for when it
  returns.

## 3.2 CloudWatch source

| Namespace | Dimensions | Metric | Statistic | Period |
| --- | --- | --- | --- | --- |
| `AWS/S3` | `BucketName`, `StorageType=<class>` (per class present) | `BucketSizeBytes` | Average | 86400 |
| `AWS/S3` | `BucketName`, `StorageType=AllStorageTypes` | `NumberOfObjects` | Average | 86400 |

Both publish once daily with a documented ~24–48h lag; the engine takes the **most recent available** daily datapoint for point-in-time signals (`stored_gb_by_class`, §3.4), not a fixed offset, and records `cloudwatch_days_covered` as the count actually seen so staleness is visible. Query only the `StorageType` values discovered present for a bucket (a per-region `ListMetrics` sweep, cached), not all ~19 unconditionally.

**The full daily series over the check window is fetched, not only the
latest datapoint** — still one free `GetMetricStatistics`/`GetMetricData`
call per `StorageType`, just returning every daily point in the window
instead of the single most recent one. This is required by §4.6's
`DIRECT_WRITE` ingest-volume estimate (N5), which needs the *trend* across
the window, not a single point.

## 3.3 CUR source

**No new Athena dataset.** The per-bucket source is a **derived view over
the existing `resource_daily` / `resource_monthly` Parquet cache**
(`pricing/cur/datasets.py`), read via DuckDB the same way
`pricing/cur/reader.py` reads every other dataset — S3 usage is already a
subset of `service_code` in those two datasets, so a new Athena query and a
new cached Parquet tree would duplicate data the cache already has.

New module `app/services/s3_bucket_source.py` (§9), one entry point:

```python
def load_bucket_rows(cache_root: Path, start: str, end: str, grain: str) -> Iterable[Row]:
    """grain: "daily" (resource_daily) or "monthly" (resource_monthly)."""
```

It runs this SQL against the dataset's existing Parquet glob (`grain`
selects `resource_daily` vs `resource_monthly` and their date column,
`usage_date` vs `billing_month`, exactly as `reader.py::build_reader_sql`
already parameterizes per-dataset date columns):

```sql
SELECT
  {date_column} AS period,                 -- usage_date | billing_month
  line_item_usage_account_id AS account_id,
  line_item_resource_id       AS bucket,
  product_region_code         AS region,
  line_item_usage_type,
  line_item_operation,
  pricing_unit,
  SUM(usage_amount)       AS usage_amount,
  SUM(net_amortized_cost) AS net_amortized_cost
FROM read_parquet(?, hive_partitioning=true, union_by_name=true)
WHERE service_code = 'AmazonS3'
  AND line_item_line_item_type = 'Usage'    -- excludes Tax/Credit/Refund/etc.
  AND {date_column} >= ? AND {date_column} < ?
GROUP BY 1, 2, 3, 4, 5, 6, 7
```

`line_item_line_item_type = 'Usage'` is required, not incidental: Tax,
Credit, and Refund rows carry the same `line_item_resource_id` and
`line_item_usage_type` as the real usage line they relate to, and letting
them into the coverage/signal computation would double-count or falsely
mark a bucket "covered" from a credit row with no corresponding usage.
**Confirmed against a captured cache**: `line_item_line_item_type` was
`'Usage'` for every S3 row in the profiled account, so the filter is kept
as specified with no observed exception to account for.

**Resource id format — evidenced.** Every profiled usage row carries the
**bare bucket name** in `line_item_resource_id` (e.g. `bucket-a` in the scrubbed fixture),
**except** `Global-Bucket-Hrs-FreeTier` rows (§3.3's registry, below),
which carry the full ARN form (`arn:aws:s3:::bucket-a`). The
normaliser strips a leading `arn:aws:s3:::` prefix from `line_item_resource_id`
before using it as the join key, so both forms resolve to the same
bucket — a bucket must never appear as two separate entities in the
result because one usage type happened to carry the ARN form.

**Region prefix — evidenced.** us-east-1 rows carry **no** region prefix
on `line_item_usage_type` (e.g. `Requests-Tier1`, not `USE1-Requests-Tier1`);
every other region does (e.g. `USE2-Requests-Tier1` for us-east-2). The
suffix-matching registry (below) already handles both forms without any
special case, since it matches on the usage type's suffix regardless of
what (if anything) precedes it — this is stated here as a confirmed fact
from the captured cache, not a change to the matching rule.

**`product_product_family` is unreliable and never used.** The captured
cache shows `product_product_family` **NULL** for transition, restore,
Intelligent-Tiering request, and monitoring rows — it is populated for
some categories and not others, with no documented rule for which. V1
never filters or groups on this column; `service_code = 'AmazonS3'` plus
the usage-type/operation registry (below) is the complete, reliable
classification path.

**The existing generic reader needs the same `union_by_name` fix.**
`pricing/cur/reader.py::build_reader_sql` builds its `resource_daily` /
`resource_monthly` reads as `read_parquet(?, hive_partitioning=true)` with
no `union_by_name` — every API endpoint that reads either dataset through
that shared function (`app/api/routes/cur.py` and others) will fail on the
same mixed-schema-months problem §3.3/§6 fixes for the new
`s3_bucket_source.py` path, as soon as `pricing_unit` is added to these
two datasets. This is not a new query to author — it is a one-line change
to an existing shared function, and it must ship in the same change that
adds `pricing_unit`, not deferred, since deferring it breaks the *existing*
CUR API surface, not just the new optimizer path (§9).

**Two additive changes to `resource_daily.sql` / `resource_monthly.sql`**
(not to any query this feature owns): (1) add `pricing_unit` to both
files' `SELECT` and `GROUP BY` and to `resource_daily` / `resource_monthly`'s
`dimensions` list in `datasets.py` — `usage_type_daily` already selects it,
and having the billing unit (`GB-Mo`, `Requests`, `GB`) explicit on every
row removes the need to infer it from the usage-type string, which the
normaliser below would otherwise have to do blind. (2) no other query
change — the two datasets already carry every other column this feature
needs.

**Correction — mixed-schema cache read and migration.** A multi-file
DuckDB `read_parquet` over months cached before and after this column is
added can fail on schema mismatch (old-schema Parquet files lack the
`pricing_unit` column entirely), and — contrary to an earlier draft's
claim — pre-existing months do **not** automatically start reading back as
`pricing_unit: null`, nor do they "naturally re-cache": `plan_refresh_tasks`
(`pricing/cur/cache.py`) only re-plans a month that is **missing**, is the
**current month**, or is inside the `--refresh-previous-days` grace window
— an already-cached historical month is left untouched indefinitely unless
`--force` is passed explicitly. Two things follow:

1. Every DuckDB read in `s3_bucket_source.py` (and any other reader of
   these two datasets) must use `read_parquet(glob, hive_partitioning=true,
   union_by_name=true)` — `union_by_name` makes DuckDB align columns by
   name across files with different schemas, so an old-schema file
   contributes `NULL` for `pricing_unit` instead of failing the whole read.
2. A `NULL` `pricing_unit` is treated as **unknown**, not as "assume GB" or
   any other guess: `normalize_usage_type` falls back to the **registry's**
   known unit for that usage-type pattern (§3.3's code table already
   states the expected unit per pattern) rather than trusting a missing
   column value.
3. **The one-time forced rebuild is an explicit operational step**, not
   automatic: `python -m pricing.cur.refresh_cur_cache --dataset
   resource_daily --dataset resource_monthly --force` (the actual flags
   `refresh_cur_cache.py`'s `parse_args` defines — `--dataset` is
   repeatable, `--force` bypasses the missing/current/grace-window
   planner logic entirely so every retained month is re-fetched from
   Athena and rewritten). Until an operator runs this, historical months
   keep serving `pricing_unit: NULL` under the `union_by_name` fallback
   above — degraded (registry-inferred units) but not broken.

**Python normalisation** (`s3_bucket_source.py::normalize_usage_type`) is
driven by the code registry `USAGE_TYPE_REGISTRY`, which contains one entry
for every row in the official AWS table, plus captured operation-specific
overlays where AWS reuses a suffix. The source table is [AWS S3 usage type
definitions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/aws-usage-report-understand.html).
Usage types carry a region prefix and are matched by suffix; `GDA` is AWS's
official abbreviation for Glacier Deep Archive. The registry's category enum
is:

| Category | Meaning |
| --- | --- |
| `storage_gb_month` | retained bytes priced by storage class |
| `storage_padding_gb_month` | observed small-object 128 KiB billing overhead |
| `storage_staging_gb_month` | temporary archive staging storage |
| `storage_overhead_gb_month` | archive index overhead kept separate from data bytes |
| `restore_copy_gb_month` | temporary restored-copy storage |
| `request` | API requests split into operation families |
| `transition` | lifecycle transition requests |
| `transition_or_restore_ambiguous` / `transition_generic` | empty lifecycle rows retained without guessing their operation |
| `restore_request` | archive restore requests by speed |
| `retrieval_gb` | archive or IA bytes retrieved; shared Glacier/Deep Archive rows remain ambiguous without the operation |
| `early_delete_gb_hours` | minimum-duration charges measured in GB-hours |
| `select_bytes` | S3 Select bytes scanned or returned |
| `batch_operations` | S3 Batch Operations jobs and object operations |
| `overwrite_gb` / `deleted_gb` | overwritten or deleted bytes by storage class |
| `data_transfer_in_gb` / `data_transfer_regional_gb` / `data_transfer_other_gb` | inbound, regional, and other transfer bytes |
| `monitoring_other` / `it_monitoring_objects` | monitoring, metadata, inventory, account, or Intelligent-Tiering monitoring usage |
| `express_one_zone` | S3 Express One Zone usage, outside V1 pricing |
| `s3_tables` | S3 Tables storage, requests, and compaction usage |
| `annotation` | annotation storage, requests, and processing |
| `other` | unknown usage retained for unmapped-usage diagnostics |

**`Requests-Tier3`/`Requests-Tier4` are only `transition_or_restore_ambiguous`/`transition_generic`
when `line_item_operation` is empty.** The captured account had it
populated on every transition/restore row observed, fully naming both the
operation type (transition vs. restore) and, for transitions, the
destination class — the earlier drafts' concern about an unresolvable
generic Tier3/Tier4 row applies only to the empty-operation case, which
this capture never exercised but which V1 still guards against, since not
every account's CUR export is guaranteed to populate the column.

**Monitoring-Automation-INT's unit is never assumed.** The captured value
(3.2258 for roughly 100 monitored objects) does not resolve cleanly to
"objects" or "thousands of object-months" by inspection — the normaliser
reads `pricing_unit` off the row itself (§3.3's `pricing_unit` column) and
uses whatever unit AWS reports, rather than guessing from the observed
ratio. If `pricing_unit` is `NULL` for this usage type in a given row
(§3.3's mixed-schema fallback), the row's `it_monitoring_objects` value is
`None`, not assumed to be either unit.

**Storage rows are keyed by operation too, with three consequences.**
`TimedStorage-ByteHrs` (and `TimedStorage-GlacierByteHrs`) is not one
signal — the same usage type carries the class's actual data bytes
(`StandardStorage`/`GlacierStorage`), its CUR-billed per-object overhead
(`GlacierS3ObjectOverhead`, `DeepArchiveS3ObjectOverhead`,
`GlacierObjectOverhead`), and its restored-copy cost
(`RestoreObject`/`DeepArchiveRestoreObject`), distinguished only by
`line_item_operation`:

1. **Overhead operations never inflate a class's data bytes.** They are
   grouped into `storage_overhead_gb_month`, tagged with the class they
   belong to, and kept **separate** from `storage_gb_month` — summing them
   into a class's `stored_gb` would double-count against §2.4's
   CloudWatch-derived `*ObjectOverhead` bytes, which already capture the
   same cost from a different source.
2. **`RestoreObject`/`DeepArchiveRestoreObject` storage rows are the
   billed temporary restored copy**, not additional storage the object
   itself accrued — this is Standard-rate storage that exists only for
   the restore's retention window. Category `restore_copy_gb_month`,
   tagged with the source archive class. It is **included in
   `per_class_observed_retrieval_cost`** (§4.3) — a restore is not free
   just because its bytes show up as `TimedStorage` rather than a
   `Retrieval-*-Bytes` row — and surfaced separately as a signal
   (§3.4/Appendix) so an operator can see "this bucket currently has an
   active restored copy costing X/month," a fact a pure retrieval-request
   count would not convey.
3. **CUR's overhead GB-months give a cross-check of the CloudWatch-derived
   per-class object count** (§2.4): for a class whose overhead was billed
   for the entire month, `objects ≈ storage_overhead_gb_month × 2^30 /
   32768` (32 KB fixed overhead per object, same constant as §2.4).
   CloudWatch remains the **primary** source for `object_count_method`
   (§2.4) — this CUR-derived figure is recorded alongside it as
   `object_count_crosscheck`, informational only, never substituted for
   the CloudWatch-derived count and never used to silently "correct" it
   when the two disagree.

**The registry lives in code, not only in this table**: a module-level
list of `((usage_type_suffix_pattern, operation_pattern), category,
storage_class, retrieval_speed)` tuples in `s3_bucket_source.py`, backed
by a fixture-driven test (§8.1) asserting every pattern here classifies
the captured usage-type/operation pairs correctly. Each row carries a
`source` of `captured` or `aws_docs`; the official rows remain in the code
registry even when the current capture has not exercised them. The table
above is an enum summary, not a second source of truth: a future AWS naming
change is fixed in one code location and the table-driven test catches drift.

**Unmapped usage types are never silently dropped** — each lands in
`category: "other"` with the raw string preserved, and the distinct set for
a bucket populates `unmapped_usage_types` in the §4 result.
`transition_or_restore_ambiguous` and `transition_generic` rows are
similarly never dropped or misclassified — both are counted separately
(§3.4) and never contribute to `transition`/`restore`, or a specific
class's transition cost; §5.1 treats their presence as a reason to
suppress an "unused" finding rather than as evidence of nothing
happening, while a disambiguated `restore` row instead makes the bucket
used outright (§5.1, below).

**Worked example — real row shapes**, based on the reference capture bucket
(`bucket-a` in the scrubbed fixture)
capture (§8.2), one bucket-day's raw rows (region prefix and
`net_amortized_cost` omitted for brevity):

| `line_item_usage_type` (suffix) | `line_item_operation` | → category / class / family |
| --- | --- | --- |
| `TimedStorage-ByteHrs` | `StandardStorage` | `storage_gb_month` / STANDARD |
| `Requests-Tier1` | `PutObject` | `requests_tier1` / STANDARD, family `data_write` |
| `Requests-Tier2` | `ReadBucketLifecycle` | `requests_tier2` / STANDARD, family `config` — **not an access signal** (§2.3) |
| `Requests-Tier2` | `GetObject` | `requests_tier2` / STANDARD, family `data_read` |
| `Requests-Tier4` | `S3-SIATransition` | `transition` / STANDARD_IA |
| `Requests-Tier3` | `RestoreObject` | `restore` / GLACIER, standard speed |
| `Retrieval-GIR` | — | `retrieval_gb` / GLACIER_IR |
| `TimedStorage-ByteHrs` | `RestoreObject` | `restore_copy_gb_month` / GLACIER (billed temporary Standard-rate copy, above) |
| `Monitoring-Automation-INT` | `IntelligentTieringStorage` | `it_monitoring_objects` / `None` (bucket-wide) |

Derived signals for this excerpt: `stored_gb_by_class` includes the
`STANDARD` bytes only from the `StandardStorage`-operation row, **not**
the `RestoreObject`-operation row (that one contributes to
`restore_copy_gb_month`/`per_class_observed_retrieval_cost` instead, above);
`monthly_config_requests` includes the `ReadBucketLifecycle` row and
**excludes** it from `requests_per_object_per_month` and every access
signal (§2.3/§4.2); the `data_read` `GetObject` row **does** count as
access; the `S3-SIATransition` row prices as a `STANDARD_IA` transition
(never a generic, unassigned Tier4 row, since the operation is present);
the `RestoreObject`-operation Tier3 row marks the bucket **used**
(§5.1, below) independent of the config-only Tier2 traffic; coverage
`true` (a `TimedStorage-*` row is present).

**Window normalisation (raw sums are never used as monthly rates).**
Checks default to a 90-day window (§5.1's `window_days`), but every
formula in §4/§5 is written in terms of one **month** of cost. Summing raw
usage over a 90-day window and comparing it directly to a monthly storage
cost silently triples request/retrieval costs, churn rates, and
thresholds relative to what a 30-day window would show — a change in
`window_days` must never change which scenario looks best. V1 therefore
defines, for every request/retrieval/egress signal and its per-class
equivalents:

```text
covered_days = count of distinct days in the window with a TimedStorage-*
               row for the bucket in resource_daily (§3.3) — the same
               per-day membership §2.1's coverage test and §12/§5.1 use,
               so "covered" means the same thing everywhere in this spec.

window_<signal>_total = raw SUM(usage_amount) over the window, restricted
                        to covered_days (a day with no TimedStorage line
                        contributes nothing to either the numerator or the
                        denominator — a coverage gap is excluded, never
                        interpolated as zero or as the period average).

monthly_<signal> = window_<signal>_total × 30 / covered_days
                    None if covered_days == 0
```

**Every `window_*_total` in this formula comes from `resource_daily`,
never `resource_monthly` — this is not optional.** `window_days` (default
90, §5.1) is an arbitrary span that generally does not align to calendar
month boundaries; a `resource_monthly` row for a boundary month carries
that **entire** month's usage regardless of how many of that month's days
actually fall inside the window, while `covered_days` (above) only counts
in-window days. Summing `resource_monthly` rows over a window and dividing
by `covered_days` would inflate the boundary months' contribution — the
exact 90-vs-30-day bias this whole subsection exists to eliminate, just
moved one level down. `resource_monthly` is used for exactly one thing in
this spec: the **12-month history arrays** in §3.5, which are explicitly
whole-calendar-month buckets, not an arbitrary window — see §3.5.

`monthly_tier1_requests`, `monthly_tier2_requests`,
`retrieval_gb_per_month_by_class`, `monthly_retrieval_cost`,
`monthly_request_cost`, and every other `monthly_*` signal in §3.4 refer
to this normalised value, never the raw window total. The raw
`window_*_total` values are retained in the result (Appendix) alongside
the normalised ones, because a check's finding text benefits from stating
the real observed total ("3,410 PUT/COPY/POST/LIST requests over 90
days") even though the *decision* math runs on the normalised monthly
rate. A partial final month in the window is handled the same way a
coverage gap is — `covered_days` only counts days actually present, so a
window that ends mid-month is not treated as a full 30 days.

**Persistence: no new database table at all.** **Correction**: an earlier
draft of this spec proposed a `s3_bucket_cur_coverage` table
`(account_id, bucket, first_day, last_day, days_with_storage_lines,
refreshed_at)`, rebuilt "at the end of `refresh_cur_cache`." Both halves
of that were wrong: `(first_day, last_day, days_with_storage_lines)` has
no per-day membership, so it cannot answer "is every day in this specific
`window_days` covered" (§5.1/§12) or identify *which* days are gap days
(§2.1's "coverage gaps excluded, never interpolated") — only a count and a
span, which is not enough. And `refresh_cur_cache.py`
(`pricing/cur/refresh_cur_cache.py`) is a standalone Parquet-refresh CLI
with no database session, no ORM models, and no "end of refresh" hook to
attach a table rebuild to — it plans and writes Parquet files
(`plan_refresh_tasks`, `pricing/cur/cache.py`) and exits.

V1 instead derives coverage **entirely on demand from `resource_daily`**,
filtered to `TimedStorage-*` usage types, through the same
`s3_bucket_source.py` DuckDB query path as every other signal (§3.3) — one
row per `(bucket, usage_date)` with a storage line is a day of coverage,
computed by grouping the query result and checking day membership
directly against the requested window. This is cheap under DuckDB (the
same access pattern every other CUR consumer in this codebase already
uses) and is cached **in-process for the duration of one scan run only**
— never written to a table, never assumed fresh across runs. The
trade-off this accepts: coverage is recomputed by scanning Parquet on
every check run rather than read from a maintained table; V1 judges that
cost acceptable given DuckDB's scan speed over the cache's typical size,
and avoids a second, easy-to-drift copy of the same fact `resource_daily`
already holds.

**Caveats.** `resource_daily`/`resource_monthly` already drop rows with an
empty `line_item_resource_id` (§10 item 9); some S3 `DataTransfer` lines
arrive that way, which is one more reason `egress_gb`/`data_transfer_out_gb`
is a **lower bound**, not a complete count (already the §4.3 assumption —
this is the same gap surfacing in a second place). On an empty cache, the
backfill window is 12 months, so `cur_first_day` reflects **the cache's
backfill start**, not the bucket's creation date, for any bucket older than
12 months; the §2.1 confidence label (which reads `cur_days_covered` within
whatever window is actually cached) is what tells an operator how much
history is really behind a number, not `cur_first_day` alone.

**Daily grain.** `cur_days_covered` and the §3.5 monthly/daily history both
read `resource_daily` with the identical `service_code`/`line_item_type`
filter above — one source function, two grains, never two divergent
filters to keep in sync.

## 3.4 Derived per-bucket signals

Computed by `s3_optimizer.py`; every field `None` independently when its inputs are unavailable, never defaulted to `0`:

* `stored_gb_by_class` — **CloudWatch `BucketSizeBytes` per class only** (§3.2), the point-in-time size that is the scenario engine's base. CUR's `storage_gb_month` is a *historical cost series* (usage-hours billed that month), not a same-quantity alternative source, so it is never substituted in — the two answer different questions and mixing them would silently change what "current size" means mid-calculation. If CloudWatch has **no datapoint at all** for the bucket, `stored_gb_by_class` is `None` and every scenario for that bucket is `status: "size_unavailable"` (§4, Appendix).
* `monthly_data_read_requests`, `monthly_data_write_requests`, `monthly_list_requests`, `monthly_config_requests` — the §3.3 window-normalisation formula applied to each family's `window_*_total` (§2.3); `None` if `covered_days == 0`. The raw `window_*_total` per family is also retained for finding text (§3.3). `monthly_tier1_requests`/`monthly_tier2_requests` (bucket-wide, all families combined, including `config`) are also reported for §4.2's pricing formulas, which price `config` requests but do not treat them as access.
* `requests_per_object_per_month` = `(monthly_data_read_requests + monthly_data_write_requests + monthly_list_requests) / total_objects` — **data-family signals only, `config` excluded** (§2.3/§4.9's checks) — normalised values, never the raw window totals; `None` if `total_objects` is `0` or `None`, or if any contributing `monthly_*` input is `None`.
* `retrieval_gb_per_month_by_class`, `retrieval_ratio_by_class` (= `monthly_retrieval_gb / stored_gb` for that class, both normalised/point-in-time; `None` if that class's `stored_gb` is `0` or `None` — undefined, not zero).
* `monthly_retrieval_cost`, `monthly_request_cost`, `monthly_storage_cost` — normalised monthly usage × resolved price per class; `None` (propagated, not summed as zero) if any contributing price is unresolved for a class with nonzero usage.
* `early_delete_cost` — for the §2.6 disclosure, not a savings input.

## 3.5 Access pattern classification

Two numbers (unused/low-access) tell a check whether to fire; they do not
tell an operator *why* a bucket looks the way it does, or whether a
recommendation should assume steady behaviour or a known burst. §4.9's
"observed periodic bursts" rule needs that distinction, so `s3_optimizer.py`
also derives a `pattern` block per bucket, all fields `None` when their
inputs are unknown.

**Source split (§3.3/N4): `history` reads `resource_monthly`, everything
else in this section reads `resource_daily`.** The 12-month `history`
arrays are explicitly **whole calendar months** — a natural fit for
`resource_monthly`'s own grain, and the boundary-inflation problem §3.3
describes for arbitrary windows does not apply here because each array
entry *is* a full month, not a window clipped mid-month. Every other
derived field below (`peak_data_read_month`, `zero_read_months`, etc.) is
computed from the same `resource_daily` rows §3.3's window-normalisation
formulas use, for the same reason those formulas require `resource_daily`.

**`data_read`/`data_write` here mean the §2.3 families, `config` requests
excluded — evidenced necessity**: the captured `ReadBucketLifecycle`/
`ReadLocation` config-request volume (§2.3) would otherwise make nearly
every scanned bucket's history look actively read every month.

* `history` — arrays, one entry per covered **calendar** month (last 12,
  or fewer if less is covered), from `resource_monthly`: `data_write`,
  `data_read`, `config`, `retrieval_gb`, `egress_gb`, `stored_gb`. This is
  the same underlying data as §3.4's aggregates, kept as a time series
  instead of collapsed to a window total, because a single total cannot
  distinguish "steady low use" from "one burst month."
* `peak_data_read_month` — the month with the highest `data_read` value, or `None`
  if every month is zero or the bucket has under 2 covered months.
* `zero_read_months` — count of months with `data_read == 0`.
* `implied_object_lifetime_days` = `total_objects / monthly_data_write_requests × 30`;
  `None` when either input is `0` or `None` (the same formula §4.7 uses for
  `MIN_DURATION_PENALTY`'s `value`, exposed here as a pattern fact rather
  than tied to one risk code).
* `growth_pct_over_window` — `(last_month_stored_gb − first_month_stored_gb)
  / first_month_stored_gb × 100`; `None` if fewer than 2 covered months or
  `first_month_stored_gb` is `0`.
* `small_object_share_estimate` — V1 has no per-object size list (§2.4), so
  this is `1.0` when `avg_object_bytes < 131072` and `None` otherwise (never
  a computed share) — a placeholder pending §10 item 2 (S3 Inventory),
  named honestly so nothing downstream mistakes it for an exact share.
* `egress_share_of_bucket` = `egress_gb / stored_gb`; `None` if `stored_gb`
  is `0` or `None`.
* `pattern_label` — one of a fixed enum, evaluated in this order (first
  match wins) so a bucket is never ambiguously double-classified:
  1. `unknown` — `telemetry_status == "unknown"`, or fewer than 30 covered
     days (too little history to classify reliably, §2.1's `confidence`
     table uses the same 30-day line).
  2. `write_once_cold` — `data_read == 0` in every covered month **and**
     `data_write` is either zero in every covered month (the load predates
     the window) or present only in the earliest covered month(s) (an
     initial load, then silence) — the classic archive candidate. A bucket
     with no reads and no writes across the whole window lands here, not in
     `unknown`: silence inside coverage is a true zero (§2.1).
  3. `log_sink` — `data_write ≫ data_read` (default: `data_write` ≥ 10×
     `data_read`) **and** the dominant `line_item_operation` is `PutObject`
     with roughly steady day-to-day volume (low coefficient of variation
     across days) — the write-heavy, read-never shape of a logging or
     replication destination (§5.1's caveat, §10 item 11).
  4. `periodic_bursts` — `peak_data_read_month ≥ 5×` the median month's
     `data_read` **and** at least 2 `zero_read_months` — recurring spikes,
     not steady traffic; this is the pattern §4.9's burst-aware
     recommendation rule looks for.
  5. `actively_read` — `requests_per_object_per_month ≥ 0.1` (§3.4,
     `config`-excluded) — too active for any cold-storage candidate to be
     a good fit regardless of storage cost.
  6. `steady_low_reads` — none of the above match but the bucket has
     nonzero, roughly consistent `data_read` every month — the default
     shape for a small-access general-purpose bucket, and the shape §4.9's
     high-confidence walk is designed for.
* `pattern_summary` — one generated sentence describing the label and its
  key supporting numbers, for direct display (e.g. "Recurring read bursts:
  the busiest month saw 8× the typical read volume, with 3 months of zero
  reads in between.").
* `write_only` (bool) — `True` when `pattern_label` is `write_once_cold` or
  `log_sink` **and** `data_read == 0` in every covered month (a true zero
  inside coverage, §2.1, not an absence) **and** `data_write > 0` in at
  least one covered month. A bucket with no writes at all in the window is
  cold, not write-only: there is no ongoing ingest for a `DIRECT_WRITE`
  candidate to act on, so it takes the ordinary ladder walk. Both labels already imply no
  reads; this flag exists so §4.9's recommendation rule and §4.6's
  `DIRECT_WRITE` candidates have a single boolean gate instead of each
  re-deriving the same condition from `pattern_label`.

All `pattern` keys are additive to the §4 result shape (Appendix) and never
gate a check on their own — `pattern_label` informs *which* recommendation
rule applies (§4.9) and improves finding text, but no check fires or
suppresses solely because of a pattern label.

---

# 4. Pricing and savings math

## 4.1 Storage cost

For **transition-based** candidates, this formula only runs at all when
§2.6's size-distribution rule allows it — `avg_object_bytes ≥ 131072`
(assume-all-eligible) or `lifecycle_small_object_override: true`
(override); otherwise the scenario is `status:
"size_distribution_unavailable"` and none of this is computed:

```text
monthly_storage_cost = Σ_class stored_gb(class) × price_per_gb_month(class)
                      + archive_overhead_cost      # Glacier/GDA/IntAA/IntDAA only, §2.4/§2.6
                      + small_object_padding_cost  # SIA/ZIA/GIR/INT-IA+ only

archive_overhead_cost = objects_in_class × 32KB × archive_rate(class)
                       + objects_in_class × 8KB  × standard_rate
                       # both terms read from CloudWatch *ObjectOverhead, §2.4;
                       # applies to Glacier, Deep Archive, and IT's Archive Access
                       # / Deep Archive Access tiers alike (IntAAObjectOverhead /
                       # IntDAAObjectOverhead, §2.4) — same 32KB archive-rate +
                       # 8KB standard-rate shape in every case

# V1 has no per-object size list, so padding is derived, never computed
# object-by-object:
small_object_padding_cost, padding_method =
    observed_*SizeOverhead_bytes × price_per_gb_month(class),  "observed_overhead"
        if the bucket already holds bytes in class (§2.4)
    else objects_in_class × max(0, 131072 − avg_object_bytes) × price_per_gb_month(class),
        "avg_size_estimate"
        if avg_object_bytes is known
    else None, None
```

**`DIRECT_WRITE_<CLASS>`** (§4.6) runs the identical formula but is never
gated by the size-distribution rule above — every object it writes is
known to go straight to the target class regardless of size, so there is
no unknown population split to worry about.

## 4.2 Request cost — by operation family, not a flat Tier1/Tier2 split

**Correction**: pricing "Tier1" as a single target-class rate is wrong
whenever the bucket issues LIST calls (§2.3) — AWS always prices LIST at
Standard's rate. §4.2 therefore reprices by the §2.3 operation families,
each independently, using **normalised monthly** values (§3.3):

**Correction — unit**: every canonical price key in this family is
`per_1000` (§2.5), so every term below **divides the request count by
1000 before multiplying** — an earlier draft's formula multiplied the raw
count directly, which overstates every request cost by 1000×:

```text
monthly_request_cost = (monthly_list_requests       / 1000) × tier1_price(STANDARD)        # always Standard, regardless of target class
                      + (monthly_data_write_requests / 1000) × tier1_price(target_class)
                      + (monthly_data_read_requests  / 1000) × tier2_price(target_class)
                      + (monthly_config_requests      / 1000) × tier1_price(STANDARD)       # priced (§2.3), but see below — never an access signal
                      + (monthly_tier1_unsplit       / 1000) × tier1_price(target_class)   # conservative fallback, §2.3
                      + (monthly_tier2_unsplit       / 1000) × tier2_price(target_class)   # conservative fallback, §2.3
```

`config` requests are priced at the Standard rate for their observed
shape (Tier1- or Tier2-billed) since a bucket-configuration read/write is
not a class-specific data operation — there is no "target class's config
rate" to move it to. Data families are **re-priced at the target class's
rates** (except `list`, always Standard) — the point of the comparison.
SIA's Tier2 (`data_read`) price is materially higher than Standard's, so
unchanged GET volume can cost more post-move before any retrieval fee is
even considered.
§8.1 includes a golden test at exactly 1,000 requests in one family
(cost should equal exactly one unit of that family's `per_1000` price) to
catch a regression of this unit error directly.

## 4.3 Retrieval cost — observed and stress, not one number

Normalised `monthly_retrieval_gb` and `monthly_restore_requests` are
persisted **by `(source_class, speed)`** (§3.4) — this is what lets the
formulas below both report the bucket's actual current retrieval bill
*and* reprice that same observed volume at a candidate's rates.

* **`current_observed_retrieval_cost`** — Correction (dimensional error):
  an earlier draft set `monthly_retrieval_cost_observed =
  retrieval_ratio_by_class`, which assigns a **dimensionless ratio**
  (`retrieval_gb / stored_gb`, §3.4) to a currency field — not
  implementable. The correct formula sums actual priced usage, **at the
  class the bucket is currently in**, per `(source_class, speed)`:

  ```text
  current_observed_retrieval_cost =
      Σ_(class, speed) monthly_retrieval_gb[class, speed] × retrieval_price[class, speed]
    + Σ_(class, speed) monthly_restore_requests[class, speed] / 1000 × restore_request_price[class, speed]
  ```

  computed only for classes the bucket is **already in** that charge
  retrieval (`retrieval_gb`/`restore_request` categories, §3.3). **The GET
  request cost is counted once, in §4.2's `read` family term — never
  added again here**; this formula prices only the retrieval-specific fee
  (per-GB) and restore-request fee, additional to, not a restatement of,
  the GET request itself. `None` if any price for a `(class, speed)` pair
  with nonzero usage is unresolved — propagated per pair, not defaulted to
  `0` for that pair while others resolve. `per_class_observed_retrieval_cost`
  — the same formula evaluated for a **single** `source_class` in
  isolation, dropping the outer sum — is retained separately for §5.3 and
  `BACK_TO_STANDARD` (below), because a mixed-class bucket's "losing
  class" must be judged on its own volumes, not the bucket-wide total.

* **`candidate_projected_retrieval_cost` — Correction (candidate repricing
  was missing)**: an earlier draft's `savings` formulas (§4.5) subtracted
  `current_observed_retrieval_cost` as if it were also the *candidate's*
  retrieval cost — but moving observed SIA retrieval traffic to Deep
  Archive, for example, must reprice that volume at Deep Archive's
  retrieval and restore-request rates, not SIA's. V1 reprices the same
  observed `(source_class, speed)` volumes at the **candidate's**
  destination class, under a **fixed speed mapping** (recorded as
  assumption `retrieval_speed_assumption`), since V1 has no signal for
  which restore speed an operator would actually choose:

  | Destination class | Speed assumed |
  | --- | --- |
  | `STANDARD_IA` / `ONEZONE_IA` / `GLACIER_IR` | instant retrieval (no restore wait — these classes have no speed tiers) |
  | `GLACIER_FLEXIBLE` | standard |
  | `DEEP_ARCHIVE` | standard |
  | `STANDARD` (`BACK_TO_STANDARD`) | `0` — Standard has no retrieval fee |

  ```text
  candidate_projected_retrieval_cost =
      Σ_(source_class, speed) monthly_retrieval_gb[source_class, speed] × retrieval_price[candidate_class, assumed_speed]
    + Σ_(source_class, speed) monthly_restore_requests[source_class, speed] / 1000 × restore_request_price[candidate_class, assumed_speed]
  ```

  `None` (propagated) wherever the source volume is `None`; when no
  class currently charges retrieval, this instead uses the same
  `data_transfer_out_gb` **lower-bound** proxy §4.3 already defines for
  the no-retrieval-history case (`retrieval_assumption:
  "egress_lower_bound"`, or `"none_observed"` if that too is absent) — it
  excludes same-region reads by CloudFront, EC2, or other AWS services, so
  it undercounts rather than overcounts. `savings_monthly_high` (§4.5)
  uses `candidate_projected_retrieval_cost`.

* **Stress**: `monthly_retrieval_cost_stress` = `full_retrievals_per_year` (param, default 1) × gb × retrieval_price(candidate_class, assumed_speed) / 12, plus one restore-request-equivalent charge per object where the candidate class requires a restore (§4.7's `*_RESTORE_LATENCY` classes) — models a single full-bucket read per year, a reasonable worst case for cold-storage candidates. The GET itself is still priced once, via §4.2. `savings_monthly_low` (§4.5) uses this.

## 4.4 Other scenario costs

```text
it_monitoring_cost       = objects_ge_128kb / 1000 × it_monitoring_price
                            # objects_ge_128kb = total_objects, computed only under
                            # the same size rule as below (§4.4/N1)
one_time_transition_cost = objects_transitioning / 1000 × transition.<destination_class>.per_1000
                            # objects_transitioning = total_objects, computed only
                            # when §2.6's size-distribution rule allows a priced
                            # transition scenario at all (avg_object_bytes ≥ 131072,
                            # assume-all-eligible; or lifecycle_small_object_override)
                            # <destination_class> is the hop's actual target class, §2.5
                            # chained policies sum this per hop, one key per hop
```

**Intelligent-Tiering scenarios use the same §2.6/N1 binary size rule as
transition scenarios**, since `it_monitoring_cost` needs `objects_ge_128kb`
and both IT savings bounds (§4.6) need eligible-byte counts, and V1 has no
per-object size list for IT either: `avg_object_bytes ≥ 131072` →
`objects_ge_128kb = total_objects`, `assumption: size_distribution:
"unknown_assumed_all_eligible"`; `avg_object_bytes < 131072` → `status:
"size_distribution_unavailable"`, no `it_monitoring_cost` or savings bound
computed. **`DIRECT_WRITE_INTELLIGENT_TIERING` is not in V1** — see §4.6.

**Transition cost scales with object count, not bytes** — the Tier3/Tier4
price is per-1000-*objects*, so a fixed number of GB spread across more,
smaller objects costs proportionally more to transition even though the
total data moved is identical. Illustrative Tier4 price $0.01/1000 objects:

```text
1,000 GB as   500,000 objects (2 MB avg):      500 × $0.01 =     $5.00 one-time
1,000 GB as 500,000,000 objects (2 KB avg):  500,000 × $0.01 = $5,000.00 one-time
```

The same object-count effect shows up in §4.1's small-object padding for
SIA/ZIA/GIR, and it has a **crossover point** worth stating exactly rather
than leaving as "small objects are worse": padding bills every object
under 128 KB as if it were 128 KB (§2.6), so a candidate class only comes
out cheaper than Standard for an object once that object's *actual* size
exceeds the point where 128 KB at the candidate's rate stops undercutting
the object's real size at Standard's rate:

```text
128 KB × price_per_gb_month(SIA) = X_KB × price_per_gb_month(STANDARD)
X_KB = 128 × price_per_gb_month(SIA) / price_per_gb_month(STANDARD)

illustrative: 128 × 0.0125 / 0.023 ≈ 128 × 0.543 ≈ 69.6 KB ≈ 70 KB
```

Below ~70 KB (using illustrative SIA/Standard prices — the real crossover
is region- and class-specific and must be computed from resolved prices,
never hardcoded), SIA's 128 KB padding makes it **cost more per object
per month than leaving the object in Standard** even before Tier1/Tier2
re-pricing or retrieval risk are considered — the storage-cost line item
alone is already worse. This is the arithmetic behind `SMALL_OBJECTS_128KB`
and the new `TRANSITION_COST_DOMINATES` risk below; both name the same
underlying problem (many small objects) at different points in the cost
model (padded storage cost vs. one-time transition cost).

## 4.5 Savings, breakeven

```text
savings_monthly_high = current_cost − (storage + request + candidate_projected_retrieval_cost + it_monitoring)
savings_monthly_low  = current_cost − (storage + request + retrieval_stress                    + it_monitoring)
savings_yearly_{low,high} = savings_monthly_{low,high} × 12

breakeven_months = one_time_transition_cost / max(savings_monthly_low, epsilon)
                    if savings_monthly_low > 0 else None
```

`breakeven_months` uses the **low** (stress) figure deliberately — an optimistic breakeven that ignores retrieval risk is the exact mistake this feature exists to prevent. `current_cost` uses `current_observed_retrieval_cost` (§4.3) — the bucket's actual current class mix — through the same §4.1–4.3 formulas, so the delta is never an artifact of two different formulas; the candidate side always reprices at the candidate's own rates (§4.3), never reuses the current class's retrieval cost as a stand-in for the candidate's.

## 4.6 Candidate policy list (V1)

| Candidate | `ladder_rank` | `ladder_group` | Notes |
| --- | --- | --- | --- |
| `STANDARD_IA` | 1 | `main` | single hop |
| `ONEZONE_IA` | 1 | `main` (side note: same rank as `STANDARD_IA`, priced similarly but flags `ONE_ZONE_DURABILITY` — single-AZ risk, never auto-recommended over `STANDARD_IA` at equal rank) | |
| `GLACIER_IR` | 2 | `main` | single hop, always-warm, no restore wait |
| `GLACIER_FLEXIBLE` | 3 | `main` | restore latency risk (§4.7) |
| `DEEP_ARCHIVE` | 4 | `main` | restore latency risk, largest discount |
| `INTELLIGENT_TIERING` (base) | `null` | `side` | see IT tier-mix bounds below; Frequent Access, Infrequent Access (30d, automatic), Archive Instant Access (90d, automatic). **The archive-tiers-configured variant is out of V1** — see below |
| `BACK_TO_STANDARD` | `null` | `side` | only when currently in IA/GIR with `RETRIEVAL_COST_EXCEEDS_STORAGE_SAVINGS`; on versioned/suspended buckets, savings are `null` — see below |
| `DIRECT_WRITE_STANDARD_IA` / `_GLACIER_IR` / `_GLACIER_FLEXIBLE` / `_DEEP_ARCHIVE` | `null` | `direct_write` | only for `pattern.write_only` buckets, §4.9. **`DIRECT_WRITE_INTELLIGENT_TIERING` is out of V1** — see below |

**Chained candidates (`CHAIN_SIA_30_GIR_90_DA_180`, `CHAIN_SIA_30_DA_180`)
are out of V1** — see §10; `ladder_group: "chain"` remains a reserved
enum value (§4.9's Appendix notes) for when they return with the §10
cohort cash-flow model, but no V1 candidate populates it.

`ladder_rank` orders the four single-hop "main ladder" candidates by
ascending storage price — deepest rank is cheapest storage but slowest/most
restricted retrieval. `INTELLIGENT_TIERING` and `BACK_TO_STANDARD` are not
part of this ordering (IT self-manages its tier, `BACK_TO_STANDARD` moves
the opposite direction) — each is `ladder_group: "side"` with
`ladder_rank: null` so a consumer never has to special-case them out of a
numeric sort.

A candidate not applicable to the bucket's current state is omitted from `scenarios[]`, never included with a null result.

**Intelligent-Tiering has no fixed tier mix — V1 must project one**, since
IT moves objects between tiers **individually**, based on each object's
own access history, which the engine cannot observe in advance — there is
no age/access distribution available (the same unavailable-distribution
problem §2.6/N1 names for lifecycle transitions) to compute a real
expected mix from. **Correction**: an earlier draft's "steady state"
language for `savings_monthly_high` (`every object ≥128 KB has gone 30+
days untouched ... and Archive Instant Access`) does not actually specify
*how many* bytes are 30–89 days old versus 90+ days old — it names two
tiers without a mix between them, which is not a number an implementation
can compute. V1 replaces this with two **explicit, clearly-labeled
theoretical bounds**, not an attempted real projection:

* `savings_monthly_low` = **all eligible bytes (≥128 KB) in Frequent
  Access** — i.e. IT costs the same as Standard plus the monitoring fee:
  `savings_monthly_low = −it_monitoring_cost`. This is the same event
  `IT_FULL_READ_PROMOTES_TO_FA` (§4.7) names as a risk — the two are the
  same fact expressed twice, once as a number in the savings range, once
  as a named risk in `risks[]`.
* `savings_monthly_high` = **all eligible bytes in the *deepest* tier
  reachable by the **base** variant**: Archive Instant Access (the
  deepest **automatic** tier). Frequent and Infrequent Access are
  intermediate points on the same path and are not separately bounded —
  the two ends of the path are what "low" and "high" mean here.

**Both bounds are labeled in the result** (a new field,
`savings_basis: "theoretical_bounds"`, on every `INTELLIGENT_TIERING`
scenario) as *"theoretical bounds; AWS moves objects individually by
access history — the bucket's real savings fall somewhere between these
two numbers, not at either endpoint."* This is deliberately not presented
as a best-estimate midpoint or any other invented point between the
bounds, because V1 has no basis to pick one. Objects below the 128 KB
floor stay at the Standard rate, unmonitored, in **both** bounds (§2.6) —
small objects never reach any IT tier beyond Frequent Access regardless
of age.

**The archive-tiers-configured variant (Archive Access / Deep Archive
Access) is out of V1** — see §10: it needs extra discovery beyond what
§3.1 already specifies, and its bounds are wide enough (§10) that they
are not actionable on their own. Only the base FA/IA/AIA variant ships.

**`DIRECT_WRITE_INTELLIGENT_TIERING` is not in V1.** §4.6's
`DIRECT_WRITE_<CLASS>` cost model (above) needs exactly **one**
`price_per_gb_month(target_class)` to price storage from day one — but
Intelligent-Tiering has no such single rate: a newly-written IT object
starts in Frequent Access and moves through tiers individually based on
its own access history, the same unresolvable-without-a-mix problem the
theoretical low/high bounds above exist to work around for the ordinary
`INTELLIGENT_TIERING` candidate. Applying the generic single-target-rate
`DIRECT_WRITE` formula to IT would silently pick one rate out of a range
with no basis for which one. §10 tracks giving direct-write IT its own
low/high bound model (the same theoretical-bounds shape as ordinary IT,
including monitoring cost) applied to a monthly ingest cohort, once that
model is worked out — V1 does not ship a mispriced approximation in the
meantime.

**Chained candidates (`CHAIN_SIA_30_GIR_90_DA_180`, `CHAIN_SIA_30_DA_180`)
are out of V1** — see §10: reporting only a final-steady-state number
without a cohort cash-flow model risks misleading an operator about
first-year value, since a chain spends 30–180 days in earlier, more
expensive classes before reaching that steady state. They return once
§10's cohort-based cash-flow model exists. `ladder_group: "chain"`
remains a reserved enum value in the meantime.

**`DIRECT_WRITE_<CLASS>` candidates** apply only to `pattern.write_only`
buckets (§3.5) and model writing *new* objects straight into the target
class with the `x-amz-storage-class` request header (`PutObject`,
multipart upload, and `CopyObject` all accept it) instead of letting a
lifecycle rule transition them later. Since the object is never written to
Standard first, it never spends 30 days there and never pays a
Tier3/Tier4 transition fee — this is a *different* cost shape from every
other candidate, not a cheaper version of the same one:

* **Storage** is at the target class's rate from day one (§4.1's per-class
  formula applied directly, no 30-day Standard interval to prorate).
* **Request cost prices only the `data_write` family at the target class's
  Tier1 rate**: `(monthly_data_write_requests / 1000) × tier1_price(target_class)`
  — the extra cost direct-write has relative to leaving the bucket alone,
  because the write itself is unavoidable. It is real: SIA's/GIR's Tier1
  PUT price is materially higher than Standard's (illustrative $0.01 vs
  $0.005 per 1000) — a doubling that must be shown, not glossed over, since
  it is the one line item that can make direct-write worse than it looks.
  **`list` stays priced at the Standard rate**, exactly as §4.2 prices it
  for every other candidate — a LIST request is not affected by which
  class new objects are written to, so `DIRECT_WRITE` does not reprice it.
* **`MIN_DURATION_PENALTY`** uses the same churn formula (§4.7) — the
  minimum-duration clock starts at write time either way, so a high-churn
  write-only bucket (frequent overwrites of the same keys) is penalized the
  same way here as under a transition candidate.
* **The scenario splits into two costs**: `transition_cost_existing` — the
  cost of moving the **backlog** of already-written objects, computed by a
  `backlog_scenario` field that references the matching main-ladder
  transition candidate (e.g. `DIRECT_WRITE_DEEP_ARCHIVE`'s backlog
  references `DEEP_ARCHIVE`) with its own `transition_cost` and
  `breakeven_months` — and `transition_cost_new_objects: 0` on the
  `DIRECT_WRITE` scenario itself, since new objects never pay a transition
  fee. This split exists because the two decisions are genuinely
  independent: an operator can adopt direct-write for all future objects
  even when moving the existing backlog is not worth it (§4.7's
  `TRANSITION_COST_DOMINATES` may fire on the backlog while the
  `DIRECT_WRITE` scenario itself has no such risk, since it has no backlog
  cost of its own).
* **Ingest volume — Correction**: an earlier draft computed
  `monthly_ingest_gb` as `monthly_tier1_requests × avg_object_bytes`,
  treating every Tier1 request as exactly one newly-ingested
  average-sized object. That is wrong on its own terms: Tier1 (in its
  `tier1_unsplit` fallback shape, §2.3) can include LIST, COPY, POST, and
  multipart part-uploads, none of which necessarily add one net-new
  retained object, and repeated overwrites of the same key add zero net
  storage per write despite each counting as a Tier1 request. V1 instead
  derives `monthly_ingest_gb` from **observed storage growth**, the one
  signal that actually reflects retained bytes: the **positive slope** of
  the CloudWatch daily `BucketSizeBytes` series over the check window
  (§3.2's full daily series, not the single latest datapoint) — a simple
  linear fit (or last-minus-first, divided by the number of days, ×30) is
  sufficient. `monthly_ingest_gb` is `None` when the slope is `≤ 0` (a
  shrinking or flat bucket has no ingest signal to size a savings claim
  against) **or** fewer than 14 datapoints are available in the window
  (too few points for a slope to mean anything). This is deliberately
  **not** derived from request counts at all.
* **Savings**: `savings_monthly_new_objects = monthly_ingest_gb ×
  (price_per_gb_month(STANDARD) − price_per_gb_month(target_class)) −
  data_write_family_request_premium` (the `data_write`-family request cost
  delta from the bullet above); `None` whenever `monthly_ingest_gb` is
  `None`. This
  is explicitly labeled in the result as **an estimate that ignores
  deletes** — a bucket with heavy churn (large deletes alongside large
  writes) will show a smaller net slope than its true write volume, which
  understates `savings_monthly_new_objects` rather than overstating it
  (the same conservative-direction choice this spec makes elsewhere,
  §4.3's stress case). Inside `backlog_scenario`, the usual
  `savings_monthly` low/high range (§4.5) still applies for whether moving
  the backlog separately pays off — that part is unaffected by this
  correction.

## 4.7 Risk codes

| Code | Meaning |
| --- | --- |
| `MIN_DURATION_PENALTY` | observed object turnover implies objects live shorter than the class's §2.6 minimum duration — see formula below |
| `SMALL_OBJECTS_128KB` | `avg_object_bytes < 131072`; `value = avg_object_bytes` |
| `GIR_FULL_RETRIEVAL_FREQUENCY` | computes `N` = full-bucket retrievals/year at which retrieval cost alone erases the storage saving; shown as `value` |
| `IT_MONITORING_DOMINATES` | monitoring cost ≥ a param share (default 50%) of the storage saving |
| `IT_FULL_READ_PROMOTES_TO_FA` | one full read moves objects to Frequent Access for 30 days — informational, not a cost |
| `DEEP_ARCHIVE_RESTORE_LATENCY` | 12h standard / 48h bulk restore; restored copy billed at Standard for the restore period |
| `GLACIER_RESTORE_LATENCY` | 1–5min expedited / 3–5h standard / 5–12h bulk, same restore-period billing note |
| `EARLY_DELETE_OBSERVED` | CUR shows an early-delete charge already paid for this bucket in the window |
| `RETRIEVAL_COST_EXCEEDS_STORAGE_SAVINGS` | observed retrieval cost alone exceeds the storage saving today |
| `TRANSITION_COST_DOMINATES` | see formula below |
| `DIRECT_WRITE_UNREADABLE_WITHOUT_RESTORE` | `DIRECT_WRITE_GLACIER_FLEXIBLE` / `DIRECT_WRITE_DEEP_ARCHIVE` only — objects written this way are immediately non-readable without a `RestoreObject` call, unlike a lifecycle-transitioned object which is at least readable for the 90/180 days before it transitions |
| `NONCURRENT_VERSIONS_INCLUDED` | fires on every scenario for a bucket whose `S3Inventory.versioning_status` is `Enabled` or `Suspended`; `value: None` |
| `BURST_RETRIEVAL_VOLUME_UNKNOWN` | §4.9 row 5 — a `periodic_bursts` bucket was sized against the generic stress parameter because no class-specific retrieval byte signal was observed to size it against the actual burst instead |
| `LOW_CONFIDENCE_COVERAGE` | `confidence == "low"` (§2.1) |

`NONCURRENT_VERSIONS_INCLUDED` exists because §2.4's correction means every
byte/object count for a versioned bucket may include noncurrent versions
this scenario's savings math was never meant to price. `reason` points at
the existing `app/checks/s3/` noncurrent-version lifecycle checks by name,
since those checks — not this one — own deciding what happens to old
versions. `value` is always `None`; the code is informational (it names a
scope limitation), not a quantified risk. **On a `BACK_TO_STANDARD`
scenario for a versioned/suspended bucket (N7, §5.3)**, `reason` is more
specific than the generic population-count caveat: it states that the
copy this scenario recommends will itself create a new billable
noncurrent version of the archived object, and that
`savings_monthly`/`savings_yearly` are `null` for exactly that reason —
this is the one case where the risk code's `reason` text changes per
scenario rather than being a fixed sentence from the §4.7 table.

`TRANSITION_COST_DOMINATES` fires when `one_time_transition_cost >
transition_payback_months_max` (param, default `12`) `× savings_monthly_low`,
**or** when `savings_monthly_low ≤ 0` while the storage saving alone
(`current_storage_cost − candidate_storage_cost`) is positive — the second
clause catches the case where the transition fee (or padding) has eaten a
real storage saving down to nothing or negative, which a breakeven-months
comparison alone would report as merely "unattractive" rather than naming
the actual cause. `value = breakeven_months` (`None` when
`savings_monthly_low ≤ 0`, matching §4.5). `reason` must name the per-object
cause explicitly, not just the dollar total, e.g.: `"500,000,000 objects ×
$0.01 per 1000 = $5,000.00 one-time; average object 2.0 KB"` — the object
count and average size are what an operator needs to see to recognize "this
bucket has too many small objects," which the dollar figure alone does not
convey.

`MIN_DURATION_PENALTY` fires from **observed churn**, not from breakeven:
`monthly_data_write_requests / total_objects > 1 / min_duration_months(class)`
— **`data_write` only, never `config` or `list`** (§2.3), since neither
bucket-configuration calls nor LIST requests replace an object; using the
bucket-wide Tier1 total (config included) would inflate the apparent churn
rate for a heavily-scanned bucket exactly the way §2.3's evidence shows.
`value = implied_object_lifetime_days
= 30 / (monthly_data_write_requests / total_objects)`, informally "how many
days an object appears to live given this write rate." `None`/no fire when
`total_objects` is `0` or `None`. `breakeven_months` (§4.5) is reported
separately in every scenario and never triggers this code — a
profitable-on-paper move can still churn objects out before the minimum
duration, which is exactly what this code catches instead. §10 item 7
(operator-supplied expected retention) remains the future refinement once
an actual lifetime is known rather than inferred from Tier1.

`SMALL_OBJECTS_128KB` uses the bucket's *average* object size because V1 has
no per-object size list (§4.1); a skewed distribution (mostly large objects
plus a long tail of tiny ones) can hide a real small-object problem behind a
mean that looks fine — §10 item 2 (S3 Inventory) is the fix that would give
an exact share instead of this average-based proxy.

Each `scenarios[].risks[]` entry is `{code, reason, value}` — `reason` is the human-readable sentence (no UI/log keeps its own copy of this table), `value` the supporting number when one exists, else `None`.

## 4.8 Worked example (illustrative — real prices come from the pricing cache)

Bucket `reports-archive`, us-east-1, **1,000 GB Standard, 500,000 objects
(avg 2 MB)**, candidate `DEEP_ARCHIVE`. Illustrative prices: Standard
$0.023/GB-month, Deep Archive $0.00099/GB-month, Tier3 transition
$0.05/1000 objects, DA standard retrieval $0.02/GB, DA standard retrieval
requests $0.10/1000.

```text
current_monthly_cost = 1000 × 0.023 = $23.00

# archive_overhead_cost: 32 KB + 8 KB per object, §2.4/§4.1
500,000 objects × 32KB = 15.259 GB  →  15.259 × 0.00099 ≈ $0.015
500,000 objects × 8KB  =  3.815 GB  →   3.815 × 0.023   ≈ $0.088
archive_overhead_cost ≈ $0.015 + $0.088 ≈ $0.10/mo

storage_cost (candidate) = 1000 × 0.00099 + 0.10 = 0.99 + 0.10 ≈ $1.09/mo

one_time_transition_cost = 500,000/1000 × 0.05 = $25.00

savings_monthly_high = current_cost − storage_cost = 23.00 − 1.09 ≈ $21.91/mo
  # no observed retrieval history: retrieval_observed = $0, "none_observed"

# stress case: one full-bucket retrieval per year, §4.3
retrieval_stress_yearly = 1000 GB × 0.02/GB + (500,000/1000) × 0.10/1000
                         = $20.00 + $50.00 = $70.00/yr ≈ $5.83/mo
savings_monthly_low = current_cost − (storage_cost + retrieval_stress)
                     = 23.00 − (1.09 + 5.83) ≈ $16.08/mo

breakeven_months = transition_cost / savings_monthly_low = 25.00 / 16.08 ≈ 1.6 months
```

No risk fires (avg_object_bytes = 2 MB ≥ 131072, so §2.6's
assume-all-eligible rule applies with `size_distribution:
"unknown_assumed_all_eligible"`; churn and duration are both comfortably
inside DEEP_ARCHIVE's window at this savings level). `status: "ok"`.

**Variant — same bucket, but 500,000,000 objects of 2 KB instead** (same
1,000 GB, radically different object count, `avg_object_bytes = 2048`).
**Under the default rule** (`lifecycle_small_object_override: false`),
`avg_object_bytes < 131072` means §2.6's size-distribution rule applies
directly: `status: "size_distribution_unavailable"`, no storage/overhead/
transition cost is computed at all, and the reason states that default
lifecycle rules skip objects under 128 KB while this bucket's population
split above/below that line is unknown from its average alone.
`SMALL_OBJECTS_128KB` still fires (`value: 2048`) — it depends only on
`avg_object_bytes`, independent of whether a transition scenario could be
priced.

**Under the override** (`lifecycle_small_object_override: true` — the
operator has explicitly stated every object, regardless of size, should
transition), the population split ambiguity is gone by definition, and
the scenario **is** priced: `archive_overhead_cost` scales with object
count, not bytes, so it becomes ≈ $15.11 + $87.74 ≈ **$103/mo** — roughly
100× the storage cost itself — and `one_time_transition_cost` =
500,000,000/1000 × 0.05 = **$25,000**. `savings_monthly_high` is strongly
negative and `TRANSITION_COST_DOMINATES` fires with `reason:
"500,000,000 objects × $0.05 per 1000 = $25,000.00 one-time; average
object 2.0 KB"`, `breakeven_months: None`. This is why the per-object
overhead, not the per-GB storage rate, dominates the DEEP_ARCHIVE decision
for buckets with many small objects **once an override forces the
scenario to be priced at all** — the scenario is still reported, not
omitted, so an operator who set the override sees *why* it was rejected.

## 4.9 Recommendation and ladder tolerance

Every `scenarios[]` entry additionally carries `tolerated_full_retrievals_per_year`
— the `GIR_FULL_RETRIEVAL_FREQUENCY` idea (§4.7) generalised to every
retrieval-charging candidate, not just GIR:

```text
tolerated_full_retrievals_per_year = storage_saving_yearly / cost_of_one_full_retrieval
  where storage_saving_yearly = (current_storage_cost − candidate_storage_cost) × 12
        cost_of_one_full_retrieval = gb × retrieval_price(class) + objects/1000 × retrieval_request_price(class)
```

`null` for `INTELLIGENT_TIERING` (no retrieval fee, §2.6) and for
`BACK_TO_STANDARD` (the target class, Standard, has no retrieval fee to
divide by). Computed for every other V1 candidate.

**Correction**: an earlier draft of this section gave five independent
bullet rules with overlapping conditions, then a follow-up draft's
"ordered decision table" had row 3 (`write_only`) instructing evaluation
to *continue* into rows 4–7 to pick a rung, while row 6 required rows 3–5
to *not* have matched — a row referencing a later row while a later row
assumes the earlier one didn't fire is not a first-match table, it's a
disguised loop. V1 fixes this by **separating rung selection from
modifiers** into two strictly sequential phases, evaluated exactly once,
in exactly this order — never re-entered, never re-ordered:

**Phase 1 — gate and select a main-ladder rung** (first matching row
terminates phase 1; if no row matches, no rung is selected and phase 2 is
skipped):

| # | Condition | Result |
| --- | --- | --- |
| 1 | `telemetry_status == "unknown"` | stop entirely: `recommended: true` on **no** scenario (§2.1); phase 2 does not run |
| 2 | `confidence == "low"` | select rung by walking the ladder **capped at `STANDARD_IA`** (`ladder_rank 1`) regardless of what a deeper rung's numbers show |
| 3 | `pattern_label == "periodic_bursts"` **and** class-specific retrieval bytes are observed for at least one archive/IA class (`retrieval_assumption` is *not* `"none_observed"`/`"egress_lower_bound"`) | select rung by walking the ladder, sizing `tolerated_full_retrievals_per_year` against the **observed peak-month** retrieval rate, not the generic stress parameter |
| 4 | `pattern_label == "periodic_bursts"` **without** a class-specific retrieval byte signal | select rung by walking the ladder against the generic stress parameter (same walk as row 5) **and** mark `burst_volume_unknown: true` for phase 2 to attach `BURST_RETRIEVAL_VOLUME_UNKNOWN` — converting Tier2 request counts into an assumed full-bucket read rate is exactly the invented-precision mistake this row avoids |
| 5 | `confidence` is `"medium"` or `"high"`, none of rows 2–4 matched | select rung by walking the main ladder from `ladder_rank 4` down to `1`, picking the **deepest** rung whose `tolerated_full_retrievals_per_year` exceeds the `full_retrievals_per_year` stress parameter (§4.3) **and** carries neither `MIN_DURATION_PENALTY` nor `SMALL_OBJECTS_128KB` |
| 6 | No rung in the main ladder satisfies row 5's tolerance condition | select **no rung**: `recommended: true` on **no** scenario, reason states no candidate's retrieval tolerance covers the assumed access pattern; phase 2 does not run |

Every row's "walk the ladder" is the same deterministic procedure (deepest
rung meeting the row's tolerance test); rows 2–5 differ only in *which*
tolerance value the walk compares against, never in the walk mechanics
itself. **Tie-break** within any walk: prefer the **deepest** `ladder_rank`
first; if still tied, prefer the **lowest** `one_time_transition_cost`.

**Phase 2 — modifiers, applied to the rung phase 1 selected** (only runs
if phase 1 selected a rung; each modifier below is independent and,
unlike phase 1, more than one can apply):

* If the selected rung carries `TRANSITION_COST_DOMINATES` **or** every
  main-ladder rung does: do not recommend that transition. If
  `pattern.write_only`, recommend the matching `DIRECT_WRITE_<CLASS>`
  candidate instead of the (dominated) plain transition. Otherwise
  recommend **no change**: `recommended: true` on **no** scenario, reason
  `"existing objects too small/numerous to move economically; consider
  aggregating objects"` (§10 item 12). This modifier is checked **first**
  — it can override every other modifier below by turning the
  recommendation into "no change" or `DIRECT_WRITE`.
* Else, if `pattern.write_only == True`: recommend that rung's
  `DIRECT_WRITE_<CLASS>` variant instead of the plain transition
  candidate; reason names the no-read window (§3.5's `backlog_scenario` is
  still reported alongside).
* Else: recommend the rung selected by phase 1 as-is.
* Independently of which of the three branches above fired, if phase 1's
  row 4 set `burst_volume_unknown`, attach `BURST_RETRIEVAL_VOLUME_UNKNOWN`
  to whichever scenario ends up `recommended: true`.

**Exactly one or zero scenarios per bucket carry `recommended: true`** —
phase 1 selects at most one rung and phase 2's three branches are mutually
exclusive, so the two phases together can produce only "no scenario" (phase
1 rows 1/6, or phase 2's dominated-with-no-write-only branch) or exactly
one. `recommendation_reason` always states in one sentence which phase-1
row and phase-2 branch fired, so "no recommendation" is as legible as a
positive one.

---

# 5. Checks

Common to all: `region` parameter; buckets with `telemetry_status == "unknown"` (§2.1) never produce a finding; `check_reason` via `create_check_reason`; metadata keys additive and stable; every skip logged with bucket name and reason. **None of the three checks prices anything or resolves prices itself** — each reads the precomputed §4 result from `resource["metadata"]["s3_optimizer"]` (§2.5), the same way existing `app/checks/s3/*.py` checks read other inventory data off the bucket dict's `metadata` key.

## 5.1 `s3_bucket_unused`

* **Candidates require coverage on *every* day of `window_days` — a deliberate, explicit exception to §2.1's "no minimum-window gate" rule, for this check only.** §2.1's general rule (findings are never suppressed for low confidence) is right for every other check, where an acted-on recommendation is reversible or low-stakes. It is wrong here: this check feeds a **deletion review**, and recommending deletion from a window with unobserved gap days risks recommending the deletion of a bucket that was actually accessed on a day this check never saw. The other two checks (§5.2, §5.3) keep the ordinary no-gate rule — this exception is scoped to `s3_bucket_unused` alone, not a general change to §2.1.
* Signal: `monthly_data_read_requests == 0` and `monthly_data_write_requests == 0` and `monthly_list_requests == 0` over the window — **`config`-family requests are never part of this signal** (§2.3/§4.2): a captured account showed config-only traffic (`ReadBucketLifecycle`, `ReadLocation`) on ~93 buckets from scanning activity alone (including MaxOps's own scans), so counting it as access would make nearly every scanned bucket look "used" regardless of real activity. `transition`/`transition_tier3`/`transition_tier4`-style category rows are also excluded — a lifecycle rule acting on the bucket is not a human or application touching it. **Correction (N8)**: an earlier draft additionally excluded ambiguous Tier3/Tier4 rows from the "used" signal outright — unsafe, since AWS documents generic `Requests-Tier3` as including **Glacier Flexible standard restore requests**, direct evidence someone is trying to read the bucket's data. The rule:
  * Any **disambiguated** `restore` row (§3.3 — `line_item_operation == "RestoreObject"`) in the window **marks the bucket used** — unambiguous access evidence, full stop.
  * Any `transition_or_restore_ambiguous` **or** `transition_generic` row (empty `line_item_operation`, §3.3) in the window means this check **cannot tell** whether the bucket is unused — unused status is `"unknown"` for this check specifically, and the finding is **suppressed** with skip reason `ambiguous_tier3_activity`.
  * Only category `transition` (operation-confirmed as a lifecycle transition, any destination class) is excluded as genuinely non-access; every other Tier3/Tier4 shape either proves access (`restore`) or is too ambiguous to clear (the two ambiguous categories above).
* **Caveat**: system-delivered writes — server access logging, S3 Inventory report delivery, cross-region/same-region replication targets — appear as ordinary `data_write`-family PUTs with no marker that distinguishes them from human/application writes, so a bucket that is purely a log or replication *destination* can look "used" by this signal even though nothing is reading it back. §10 item 11 tracks distinguishing system-delivered writes via `line_item_operation` where AWS emits a distinguishing value.
* Params: `window_days` (default 90).
* Empty case: **any** day in `window_days` lacking a `TimedStorage-*` row (per §3.3's `covered_days` membership) → skip reason `window_not_fully_covered` (not merely "outside coverage" — a bucket can be inside CUR coverage in the §2.1 sense, with data present for *most* of the window, and still fail this check's stricter full-window requirement). A bucket with **zero** coverage in the window is still `telemetry_status: "unknown"` (§2.1) and never reaches this check at all. `ambiguous_tier3_activity` (above) is a separate, later skip reason, checked after full-window coverage is confirmed.
* Finding fields: `requests_tier1_total`, `requests_tier2_total`, `window_days`, `stored_gb`, `bucket_age_days`, `confidence`.
* `recommended_action: "s3_review_unused_bucket"` — advisory: suggests deletion review, or (if retention is required) attaches a `DEEP_ARCHIVE` scenario as the cheapest retain-it alternative.
* Savings: full current storage cost; `DEEP_ARCHIVE` scenario shown as the alternative.

## 5.2 `s3_bucket_low_access`

* Candidates: general-purpose buckets inside coverage, `stored_gb ≥ min_bucket_gb` (default 1).
* Signal: `requests_per_object_per_month < requests_threshold` (0.01) **and** every class's `retrieval_ratio_by_class < retrieval_threshold` (0.1).
* Empty case: `requests_per_object_per_month` is `None` → skip `object_count_unknown`, never flagged.
* Finding fields: `requests_per_object_per_month`, `retrieval_ratio_by_class`, `storage_class_breakdown`, `confidence`.
* `recommended_action: "s3_review_storage_class"` — attaches the full ranked `scenarios[]` (§4) so the operator chooses, rather than the check picking a candidate for them.
* Savings: best `savings_monthly_high` among candidates with `status != "not_beneficial"` and no `MIN_DURATION_PENALTY`.

## 5.3 `s3_bucket_retrieval_cost_dominant`

* Candidates: buckets currently holding bytes in SIA, ZIA, GIR, Glacier Flexible, or Deep Archive.
* Signal: `(per_class_observed_retrieval_cost[losing_class] + request_cost_delta_vs_standard) > (standard_storage_cost_equivalent − current_class_storage_cost)`, using **observed** data for that **single losing class** (§4.3's `per_class_observed_retrieval_cost`, not the bucket-wide sum — a mixed-class bucket's other classes must not dilute this class's own evaluation), not the §4.3 stress case.
* Empty case: retrieval price unresolved (§2.5) → skip `retrieval_price_unavailable`, never flagged on an assumed-zero cost.
* Finding fields: losing class, `per_class_observed_retrieval_cost`, `standard_storage_cost_equivalent`, `current_class_storage_cost`, `confidence`.
* `recommended_action: "s3_restore_to_standard"` — advisory, shaped by the losing class:
  * **SIA / ZIA / GIR** (always-warm classes): in-place `CopyObject` to `STANDARD` via S3 Batch Operations.
  * **GLACIER / DEEP_ARCHIVE**: `CopyObject` cannot read these classes directly — a `RestoreObject` call must create a temporary Standard-billed copy first (12h/48h or 3–5h/5–12h latency per §4.7), *then* the Batch Operations copy runs against the restored copy. The advisory text says "restore, then copy" explicitly and includes the restore-window Standard-rate cost, not just the Batch Operations fee.
  * All variants must state: (1) the copy (and, for archive classes, the preceding restore) is one more retrieval, plus an early-delete charge if within the §2.6 minimum duration; (2) Batch Operations job/per-object fees apply; (3) **lifecycle rule removal must precede the copy**, or objects simply transition back on schedule and the retrieval cost is wasted.
  * **New — versioned/suspended buckets (N7)**: a `CopyObject` to the same key in a bucket with `versioning_status` `Enabled` or `Suspended` does not replace the archived object — it creates a **new current version** at the Standard rate while the archived object **becomes a billable noncurrent version**, continuing to accrue its archive-class storage (and, for Glacier/Deep Archive, its minimum-duration exposure) rather than disappearing. The naive savings formula below assumes the losing-class cost goes away; on a versioned bucket it does not.
* **Savings**: `current_class_storage_cost + per_class_observed_retrieval_cost[losing_class] − standard_storage_cost_equivalent`, monthly only — no yearly claim when the premise is "stop the bleeding now." **On a versioned/suspended bucket, this is `null`** with a reason explaining the noncurrent-version cost above, and the `s3_restore_to_standard` advisory additionally **requires a noncurrent-version expiration rule** (or an explicit accepted-cost acknowledgment) before the copy is a real saving — without one, the "restore" recommendation would leave the archived cost in place under a new name while adding a Standard-rate copy on top of it. Unversioned buckets are unaffected and keep the quantified savings formula above. `NONCURRENT_VERSIONS_INCLUDED` (§4.7) fires on this scenario for a versioned/suspended bucket with updated reason text pointing specifically at this interaction, not only the generic §2.4 population-count caveat.

---

# 6. Actions

All **four** are **advisory only** in V1 — no handler executes a mutation. Each requires an action-capture scenario in `actions.json` per the `tests_generator` action contract (§8), so the same fixture is ready whenever a future V2 promotes one to executable.

**Correction — how "advisory, no handler" is actually registered.**
`ActionMetadata.handler` (`app/actions/registry.py`) is currently a
**mandatory** field of type `ActionHandler` (a callable), and the
execution path calls it directly — there is no way to register an action
with `handler=None` today, and the earlier draft's "no handler executes a
mutation" was not backed by an actual registration mechanism. V1 extends
`ActionMetadata` with `executable: bool = True` and makes `handler:
Optional[ActionHandler] = None`. The four actions in this section register
with `executable=False, handler=None`; the **execution path** (wherever
`ActionRegistry` resolves and invokes a handler) is changed to reject a
call against an `executable=False` action with a clear error **before**
attempting to call `handler` (never a silent no-op, never an
`AttributeError` on `None`). Catalog/list surfaces that enumerate actions
(§7) show `executable` so a UI can distinguish "click to run" from
"advisory, read the finding" without a special-cased action-ID list.

* `s3_review_unused_bucket` — deletion-review recommendation, plus the §5.1 `DEEP_ARCHIVE` retain-it scenario when attached.
* `s3_review_storage_class` — the §5.2 ranked `scenarios[]` list; any proposed lifecycle rule text states `ObjectSizeGreaterThan=131071` explicitly (§2.6) so the rule an operator would create matches what the scenario priced.
* `s3_restore_to_standard` — the §5.3 recommendation with all three caveats (copy cost, Batch Operations fees, rule-removal ordering) present verbatim in the advisory text; each is a way the recommendation goes wrong if skipped, not boilerplate.
* `s3_write_direct_to_class` — attached to a `DIRECT_WRITE_<CLASS>` recommendation (§4.9): tells the operator which `x-amz-storage-class` header value to set in the producer (`STANDARD_IA`, `GLACIER_IR`, `GLACIER`, `DEEP_ARCHIVE` — **not** `INTELLIGENT_TIERING`, §4.6); states that objects written to `GLACIER`/`DEEP_ARCHIVE` this way are **immediately non-readable without a restore** (`DIRECT_WRITE_UNREADABLE_WITHOUT_RESTORE`, §4.7); and states that keeping a lifecycle rule in place is still worthwhile **as a backstop** for any producer path that forgets to set the header — the advisory does not recommend removing existing lifecycle rules the way `s3_restore_to_standard` does, because here the rule and the header serve complementary, not conflicting, purposes.

---

# 7. Registry, catalog, UI

* Register each check with `resource_type="s3"` and the §5 parameters.
* Policy seeding is additive: startup and `/seed` add missing registered-check rows without modifying existing policy rows unless `force=True`.
* Add rows to `docs/maxops_checks_catalog.csv` (header: `Resource Type,Check ID,Check Name,Description (one-liner),Default Action,Payload Generation,Payload Scenarios,Test Cases,Test Type,Dedicated Test Files,Parameters`) for all three checks, `Resource Type=s3`.
* Add the four advisory actions (§6, including `s3_write_direct_to_class`) to `docs/maxops_actions_catalog.csv`.
* **Backend API (new — required before MCP can exist)**. **Correction**:
  an earlier draft specified only the MCP client/server files, with no
  backend route, service entry point, lookup-key contract, or router
  mount — there was nowhere for `get_s3_optimizer_detail` to actually get
  data from, and a route that is never mounted is unreachable regardless
  of how it's written. V1 adds `app/api/routes/s3_optimizer.py`: `GET
  /s3-optimizer/{inventory_id}` — looks up `S3Inventory` by
  `inventory_id` using `db: Session = Depends(get_db)`, the **same session
  dependency every other route module uses** (e.g.
  `app/api/routes/inventory.py`'s `s3_inventory_overview(db: Session =
  Depends(get_db))`), returns **404** if no such inventory row exists, and
  otherwise returns the **stored** §4 JSON shape (Appendix) from
  `S3Inventory.metadata_json["s3_optimizer"]` (§2.5's dependency path — the
  route does not price anything itself) for that bucket, **including**
  `telemetry_status: "unknown"` results — an unobserved bucket is a valid,
  complete answer, never a 404. **No recompute flag** — the stored scan
  result is served as-is (§10 tracks an on-demand recompute path as a
  future item, cut from V1 because the stored result is sufficient and
  recomputing on request would duplicate the scan pipeline's own
  orchestration). The route calls a new service entry point in
  `app/services/s3_optimizer.py`, `get_bucket_detail(db: Session,
  inventory_id: int, cache_root: str)` — **takes the DB session**, since
  the `S3Inventory` lookup this endpoint promises cannot happen without
  one — and `cache_root` defaults to `pricing.cur.datasets.DEFAULT_CACHE_ROOT`
  as a query parameter, matching the exact pattern `app/api/routes/cur.py`
  already uses for every CUR-backed endpoint (its routes take `cache_root:
  str = str(DEFAULT_CACHE_ROOT)` / `Query(default=str(DEFAULT_CACHE_ROOT))`)
  — not a new configuration mechanism. **The router must be mounted in
  `app/main.py`, next to the CUR router** — every reachable router in this
  codebase is explicitly imported (`app/main.py` line 6's import list) and
  mounted with `app.include_router(...)` (e.g. line 48:
  `app.include_router(cur.router, prefix="/api/v1")`); `s3_optimizer` is
  added to both the import list and a new `app.include_router(s3_optimizer.router,
  prefix="/api/v1")` call beside `cur`'s. Skipping this step is the
  single most common way a correctly-written FastAPI route silently
  404s in this codebase.
* **MCP**: a `get_s3_optimizer_detail(inventory_id: int) -> Any` tool in
  `maxops-mcp/src/maxops_mcp/server.py`, following the exact
  `get_rightsizer_resource_detail` pattern already in that file (thin
  `async with MaxOpsClient() as client: return await
  client.get_s3_optimizer_detail(inventory_id)`), backed by a new
  `MaxOpsClient.get_s3_optimizer_detail` method in
  `maxops-mcp/src/maxops_mcp/client.py` following
  `get_rightsizer_resource_detail`'s pattern there (`return await
  self._request("GET", f"/s3-optimizer/{inventory_id}")`) — both files
  already establish this exact thin-wrapper shape for every other detail
  tool, so this is a new instance of an existing pattern, not a new one.
* **Where it links in the hub/UI is deferred** — tracked in §10. This spec fixes the data contract (§4) precisely so that decision can be made later without touching the engine.

---

# 8. Testing

Same three-layer shape as the SageMaker precedent (§8 there): unit tests free and immediate; payload-backed tests replay captured fixtures; nothing runs a generator that creates AWS resources without explicit operator action.

## 8.1 Unit tests — logic, free, immediate

`tests/test_s3_optimizer.py`, table-driven, at minimum:

* **Usage-type normaliser** (§3.3): one case per category, including a region-prefixed variant to confirm suffix matching, plus one unrecognized usage type routed to `other` and surfaced in `unmapped_usage_types`.
* **Request cost unit** (§4.2): exactly 1,000 requests in one family (e.g. `monthly_data_write_requests = 1000`, all others `0`) → `monthly_request_cost` equals exactly that family's `per_1000` price, catching a regression of the divide-by-1000 step directly.
* **Config requests excluded from access signals** (§2.3/§4.2, evidenced by the `ReadBucketLifecycle`/`ReadLocation` capture): a bucket with only `config`-family Tier1/Tier2 traffic and zero `data_read`/`data_write`/`list` → `requests_per_object_per_month == 0` (or `None` if `total_objects` unknown), `s3_bucket_unused` still eligible to fire, `config_requests` nonzero in `window_totals`/`signals`; a mixed bucket with both → only the data-family counts feed the access signal.
* **Overhead-derived counts** (§2.4): known overhead bytes → exact `objects_glacier`; single non-archive class → `"remainder_derived"`; two non-archive classes → byte-share split checked against a hand-computed value.
* **Scenario math** (§4): the §4.8 2 MB-avg case as a golden-number test (`status: "ok"`, `size_distribution: "unknown_assumed_all_eligible"`, positive savings, no risks); the 2 KB-avg case **under the default rule** (`status: "size_distribution_unavailable"`, no storage/transition cost computed, `SMALL_OBJECTS_128KB` still fires on `avg_object_bytes` alone); the same 2 KB-avg case **under `lifecycle_small_object_override: true`** (priced, `TRANSITION_COST_DOMINATES` fires with `reason` naming the object count, per-1000 price, and average object size, `value == breakeven_months == None`); a `MIN_DURATION_PENALTY` case using the churn formula (§4.7) where implied object lifetime is shorter than the candidate's minimum duration.
* **Absent-is-not-zero** (§2.1): zero CUR rows → `"unknown"`, no finding; a coverage gap month excluded from `cur_days_covered`, never interpolated; a bucket with only unmapped usage types → all signals `None`, `unmapped_usage_types` populated.
* **Pattern classifier** (§3.5): one case per `pattern_label`, in evaluation order — `unknown` (under 30 days covered wins even when other rules would also match); `write_once_cold` (initial-month data_write only, data_read always 0); `log_sink` (steady daily PutObject volume, data_write ≫ data_read); `periodic_bursts` (one month ≥5× median data_read, 2+ zero-read months); `actively_read` (high requests-per-object overrides a lower-priority match); `steady_low_reads` (the fallthrough case). Plus `implied_object_lifetime_days` and `egress_share_of_bucket` on `None`-input cases.
* **Write-only detection** (§3.5): `write_once_cold` and `log_sink` cases with `data_read == 0` every covered month → `pattern.write_only == True`; a `log_sink`-shaped bucket with even one nonzero-`data_read` month → `write_only == False`; a bucket with heavy `config` traffic but zero `data_read`/`data_write` still classifies correctly since `config` is excluded from the inputs above.
* **`DIRECT_WRITE` cost model** (§4.9): a golden-number case showing the SIA Tier1 write-family PUT premium ($0.01 vs $0.005/1000, illustrative) as the only request cost delta versus doing nothing, `list` requests still priced at Standard, `transition_cost_new_objects == 0`, and a populated `backlog_scenario`; a separate case for `monthly_ingest_gb`: a daily `BucketSizeBytes` series with positive slope over ≥14 datapoints → nonzero `savings_monthly_new_objects`; a flat/declining series or <14 datapoints → `monthly_ingest_gb: None` and `savings_monthly_new_objects: None`.
* **`TRANSITION_COST_DOMINATES` golden case** (§4.7): covered by the override-case scenario-math bullet above — the non-override 2 KB case never reaches a priced scenario to dominate, so this risk is asserted only against the override variant.
* **Recommendation picks `DIRECT_WRITE`** (§4.9): a `write_only` bucket where the ladder walk would otherwise select `DEEP_ARCHIVE` → recommended scenario is `DIRECT_WRITE_DEEP_ARCHIVE`, not `DEEP_ARCHIVE`, with `recommendation_reason` naming the observed no-read window.

## 8.2 Payload-backed tests — real AWS shapes, captured once

`tests_generator/s3_optimizer/` per `tests_generator/PAYLOAD_GENERATION.md`: `resource_config.json`, `checks.json`, `actions.json`, resource-creation and payload-generator scripts, `s3_optimizer` entries in `payload_check_map.json`, following the existing `tests_generator/s3/` layout (additional fixtures, not a replacement for the existing `s3_*` lifecycle fixtures). Fixtures are **CUR rows plus CloudWatch daily-storage JSON**, not object bytes. As with SageMaker, **no generator may create real buckets or CUR data without the shared `require_capture_gate`**.

**CUR fixtures are captured from the refreshed local cache and scrubbed by
script, not hand-typed.** A new `pricing/cur/tools/scrub_s3_fixture.py`
reads captured rows out of `data/cur_cache` (`resource_daily`/
`resource_monthly`, §3.3), replaces account ids and bucket names with
fixture-safe values, and writes the fixture — **usage types, operations,
and amounts are kept verbatim**, since those are exactly what the
normaliser and pricing formulas need to exercise real shapes; only
identifying values are scrubbed. **The reference fixture is the reference
capture bucket's (`bucket-a` in the scrubbed fixture) 75-day capture**:
transitions occur on
day 2 (`S3-SIATransition`/`S3-ZIATransition`/`S3-GIRTransition`/
`S3-INTTransition`/`S3-GlacierTransition`/`S3-GDATransition`), restores
and retrievals on day 4, restored-copy storage (`restore_copy_gb_month`,
§3.3) on days 5–7, and Intelligent-Tiering monitoring from day 3 — one
fixture exercising every transition/restore/retrieval/monitoring category
this spec's registry defines, on a known timeline unit tests can assert
exact per-day expectations against. The broader captured sample (used for
the evidence cited throughout this spec, not as a fixture itself) covers
**29 buckets with storage lines, 20 of them with zero data-family
(`data_read`/`data_write`/`list`) requests in the window** — real-world
confirmation that config-only traffic on an otherwise-idle bucket is the
common case, not an edge case, for §2.3/§5.1's exclusion rule.

Until fixtures exist, payload-backed tests **skip** with `"s3 optimizer payload fixtures not captured; run tests_generator/s3_optimizer"` — never fail, never `xfail`.

## 8.3 What is deliberately not simulated

No execution path exists in V1 (§1.1, §6), so there is no action-replay harness beyond the capture-only `actions.json` scenarios in §6. A handler added later (§10) gets its own replay suite then.

---

# 9. Module layout

```text
app/services/s3_bucket_source.py            derived-view reader over resource_daily/resource_monthly Parquet, usage-type normaliser (§3.3)
app/services/s3_optimizer.py                scenario engine, pattern classifier, derived signals, get_bucket_detail(db, inventory_id, cache_root) (§3.4, §3.5, §4, §7)
app/services/scan_service.py                NEW S3 enrichment step (alongside _enrich_ec2_rightsizing_metrics, called from the same inventory-phase site ~line 1835): CUR-derived price map (app/pricing/s3_price_map.py, no session) once per region, s3_optimizer engine once per bucket, result written to S3Inventory.metadata_json["s3_optimizer"] + price_catalog_version AND to CachedAWSAdapter.overlay_resource_metadata(...) (below) on the same per-region adapter the policies phase reuses (§2.5)
app/services/scan_service.py (CachedAWSAdapter)   NEW overlay_resource_metadata(resource_type, resource_id, key, value): mutates the cached get_resources payload in place so later get_resources calls (deep-copied from that same cache) include it (§2.5)
app/checks/s3/bucket_unused.py              s3_bucket_unused — reads resource["metadata"]["s3_optimizer"], prices nothing (§2.5, §5)
app/checks/s3/bucket_low_access.py          s3_bucket_low_access — same
app/checks/s3/bucket_retrieval_cost_dominant.py   s3_bucket_retrieval_cost_dominant — same
app/checks/s3/s3_optimizer_spec.md          (this file)
pricing/cur/queries/resource_daily.sql      additive: + pricing_unit (§3.3)
pricing/cur/queries/resource_monthly.sql    additive: + pricing_unit (§3.3)
app/pricing/pricing_s3.py                   estimate_s3_storage_cost gains optional price_map param (sessionless signature unchanged); handle_s3_pricing reworked onto s3_price_map.py (§2.5); constants retired to price_map-absent fallback only, marked deprecated
app/pricing/s3_price_map.py                  CUR-derived price map: resolve(rows, region, window), seed-table fallback, no DB session (§2.5); replaces the earlier Pricing-API-based s3_price_catalog.py
pricing/s3_price_seed.json                  hand-maintained seed table, (region, canonical_key) -> {price, unit, as_of, source_url} (§2.5)
app/adapters/aws/adapter.py                 free CloudWatch daily storage metrics per StorageType + NumberOfObjects (§3.2); NEW get_s3_bucket_lifecycle_typed (existing raw-dict lifecycle method untouched, still used by the 8 app/checks/s3/*.py consumers); NEW list_bucket_intelligent_tiering_configurations (§3.1, §13)
pricing/cur/datasets.py                     + pricing_unit in resource_daily/resource_monthly dimensions (§3.3)
pricing/cur/reader.py                       build_reader_sql: + union_by_name=true, so existing CUR API reads survive mixed-schema months (§3.3, §9)
app/checks/__init__.py                      registry entries for the three checks
app/actions/registry.py                     ActionMetadata.executable/handler changes (§6, §14); four advisory actions registered with executable=False, handler=None
app/api/routes/s3_optimizer.py               GET /s3-optimizer/{inventory_id} (§7)
app/main.py                                  import + app.include_router(s3_optimizer.router, prefix="/api/v1") beside the cur router (§7)
maxops-mcp/src/maxops_mcp/client.py           MaxOpsClient.get_s3_optimizer_detail, following get_rightsizer_resource_detail's pattern (§7)
maxops-mcp/src/maxops_mcp/server.py           get_s3_optimizer_detail tool, following get_rightsizer_resource_detail's pattern (§7)
docs/maxops_checks_catalog.csv, docs/maxops_actions_catalog.csv   catalog rows (§7)
app/models/inventory.py                     S3Inventory.metadata_json additions (§3.1) + "s3_optimizer" key, price_catalog_version stamp = (price_map_window, seed_as_of) (§2.5)
tests/test_s3_optimizer.py
tests/test_s3_optimizer_payload_checks.py   (skips until captured)
tests_generator/s3_optimizer/
```

Reuse, do not copy: `DatasetDefinition`/dataset registration and `resource_daily`/`resource_monthly` (`pricing/cur/datasets.py`, `pricing/cur/reader.py`), `create_check_reason`, `pricing/cur/refresh_cur_cache.py` and `pricing/cur/cache.py::plan_refresh_tasks` (Parquet refresh only — no database hook, §3.3), and the existing `app/checks/s3/` lifecycle module for how S3 checks currently read `S3Inventory`.

### Implementation notes

The Phase 4 implementation records these deliberate reconciliations with the design above: the adapter method is named `list_s3_bucket_intelligent_tiering_configurations`; the persisted `signals` keys are `monthly_data_read_requests`, `monthly_data_write_requests`, `monthly_list_requests`, `monthly_config_requests`, `monthly_tier1_requests`, `monthly_tier2_requests`, `restore_requests_by_class`, `retrieval_gb_per_month_by_class`, `requests_per_object_per_month`, `retrieval_ratio_by_class`, `per_class_observed_retrieval_cost`, `standard_storage_cost_equivalent`, `current_class_storage_cost`, `request_cost_delta_vs_standard`, `early_delete_cost`, `config_requests`, and `window_totals`; `current.storage_class_breakdown` omits classes with no CloudWatch datapoint; and `coverage.covered_days` persists the exact covered ISO dates.

`DIRECT_WRITE_INTELLIGENT_TIERING` was removed from V1; chained candidates and the configured-Intelligent-Tiering variant remain deferred. The seed table currently covers `us-east-1` only. Live scans on 2026-09-21/22 covered 47 buckets across two regions: 28 were usable, `s3_bucket_unused` fired on 11, `s3_bucket_low_access` fired on 0 because all cold buckets were below `min_bucket_gb=1`, and `s3_bucket_retrieval_cost_dominant` fired on 0.

---

# 10. TODO — open items after V1 implementation

**Status: open. Owner: operator / next contributor.**

1. **Where S3 Optimizer links in the hub/UI** — deferred by §7; a product decision, not engineering.
2. **Storage Lens / S3 Inventory** — would replace the §2.4 byte-share estimate with exact per-class, per-prefix counts.
3. **Daily grain** — all window-scoped signals already read `resource_daily` (§3.3/N4 requires this for correctness, not as an optimization); `resource_monthly` is used only for the §3.5 12-month calendar-month history arrays. A fully daily-grain *history* (replacing §3.5's monthly buckets with daily ones) would sharpen the pattern classifier's resolution for bursty buckets, at the cost of more DuckDB scan volume per check run.
4. **Prefix-level scenarios** — today's scenarios are bucket-wide; needs prefix-level CUR grouping (unavailable today) or S3 Inventory (item 2).
5. **Versioned-bucket noncurrent-version split** — out of scope for V1 (§1.2), owned by the existing lifecycle checks.
6. **One Zone-IA durability policy** — `ONE_ZONE_DURABILITY` fires unconditionally; a per-bucket "this data is rebuildable" input would let the engine suppress it selectively.
7. **Expected object lifetime input** — an operator-supplied retention period would let `MIN_DURATION_PENALTY` compare against actual plans instead of only the AWS-mandated minimum duration.
8. **Intelligent-Tiering archive-tiers-configured variant** — cut from V1 (§4.6): it needs discovery beyond what §3.1 already specifies (the operator's actual configured day thresholds, already parsed, are necessary but not sufficient to make the resulting bounds narrow enough to act on) and its theoretical bounds were judged too wide to recommend from. Returns once that's resolved; whether a *recommendation* should also suggest adding a configuration to a bucket that has none is a further, separate open question.
9. **Requester Pays / cross-account CUR resource-id gaps** — some `line_item_resource_id` values are blank for certain billing configurations; those buckets fall into `"unknown"` by design (§2.1) today. A targeted ARN-based fix is unexplored.
10. **The §4 JSON result shape is frozen** — new keys may be added at any level; existing keys are never renamed or removed without a migration.
11. **Distinguish system-delivered writes** (§5.1) — server access logging, S3 Inventory delivery, and replication all land as ordinary Tier1 PUTs today, so a log/replication-destination bucket can score "used" while nothing reads it back. Investigate whether `line_item_operation` (or another CUR field) reliably distinguishes these; if so, exclude them from the `s3_bucket_unused` signal.
12. **Object aggregation guidance** — when `TRANSITION_COST_DOMINATES` fires with no `write_only` escape hatch (§4.9), V1's only recommendation is "consider aggregating objects." It does not identify *which* objects, propose a bundling scheme (e.g. periodic tar/zip-and-upload of a prefix), or implement any aggregation job. A future version could turn this from a one-line suggestion into a concrete, prefix-scoped plan once prefix-level scenarios (item 4) exist to scope it.
13. **Cohort-based cash flow model for chains** (§4.6) — V1 reports a chain candidate's final-steady-state monthly savings with `savings_yearly: null` rather than modeling the ramp. A full fix tracks ingest cohorts through each hop's timeline (days in each earlier, more expensive class; hop costs when actually incurred; minimum-duration interactions per hop) to produce an honest first-12-months cash flow instead of an all-or-nothing steady-state number.
14. **Tier-aware storage pricing** (§2.5) — V1 always uses the first storage-price tier on both the current and candidate side (`pricing_tier: "first_tier"`), which can overstate the Standard baseline (and therefore the claimed savings) for a bucket large enough to reach a cheaper volume tier, and does not account for consolidated billing across an organization. A fix would parse all price dimensions and compute the before/after billing delta using the bucket's (or account's) actual aggregate usage against the applicable tier boundaries.
15. **`DIRECT_WRITE_INTELLIGENT_TIERING`** (§4.6) — excluded from V1 because it has no single target storage rate to price from day one. A future version would apply the same theoretical low/high bound model §4.6 defines for ordinary `INTELLIGENT_TIERING` (including monitoring cost) to a monthly ingest cohort instead of a point-in-time balance, giving direct-write IT its own bounded, not single-scalar, savings result.
16. **Registry capture reconciliation** (§3.3) — the registry was rebuilt from the official AWS table on 2026-09-22. The only remaining unverified assumption is which `line_item_operation` values accompany the shared retrieval-byte rows for Deep Archive; confirm on the first real Deep Archive restore.
17. **Pricing API resolution for keys absent from both CUR and the seed table** (§2.5) — V1's price map has exactly two sources; a canonical key with no billed CUR usage in the window and no seed entry is `pricing_unavailable` with no third fallback. A future version could call the Pricing API (the design this spec replaced, §2.5) as a last resort for that narrow gap only, rather than as the primary source.
18. **Chained candidates `CHAIN_SIA_30_GIR_90_DA_180`/`CHAIN_SIA_30_DA_180`** (§4.6) — cut from V1: reporting steady-state-only numbers without item 13's cohort cash-flow model would mislead an operator about first-year value. Return together with item 13.
19. **`GET /s3-optimizer/{inventory_id}` recompute flag** (§7) — cut from V1: the stored scan result is sufficient, and an on-demand recompute would duplicate the scan pipeline's own orchestration (§2.5). Add only if a real need for on-demand freshness emerges.
20. **S3 street-pricing release coverage** (§2.5) — `street_pricing_s3` is generated for the 15 `TARGET_REGIONS` by `staging_pricing/import_s3_pricing.py`; onboarding a region means adding it to `TARGET_REGIONS` and rerunning the importer. The runtime still never calls the Pricing API.

---

# Appendix: §4 JSON result shape (one bucket)

```jsonc
{
  "inventory_id": 0, "resource_id": "reports-archive", "account_id": "111111111111",
  "region": "us-east-1", "telemetry_status": "usable", "confidence": "medium",
  "coverage": {"cur_days_covered": 62, "cur_first_day": "2026-06-01",
    "cur_last_day": "2026-09-01", "cloudwatch_days_covered": 60},
  "current": {
    "storage_class_breakdown": {"STANDARD": {"bytes": 1073741824000, "objects": 500000,
      "object_count_method": "remainder_derived", "avg_object_bytes": 2097152.0}},
    "monthly_storage_cost": 23.0, "monthly_request_cost": 0.01,
    "monthly_retrieval_cost": null
  },
  "signals": {
    "monthly_tier1_requests": 200, "monthly_tier2_requests": 0,
    "requests_per_object_per_month": 0.0004, "retrieval_ratio_by_class": {},
    "early_delete_cost": null, "config_requests": 340,
    "window_totals": {
      "covered_days": 62,
      "tier1": {"list": 40, "data_write": 160, "config": 220, "tier1_unsplit": 0},
      "tier2": {"data_read": 0, "config": 120, "tier2_unsplit": 0},
      "config_requests": 340,
      "retrieval_gb": {},
      "restore_requests": {},
      "egress_gb": 0.0
    }
  },
  "checks_triggered": ["s3_bucket_low_access"],
  "scenarios": [{
    "policy": "DEEP_ARCHIVE", "status": "ok",
    "ladder_rank": 4, "ladder_group": "main",
    "recommended": false, "recommendation_reason": "write_only bucket; DIRECT_WRITE_DEEP_ARCHIVE avoids the transition fee for new objects",
    "tolerated_full_retrievals_per_year": 3.76,
    "monthly_costs": {"storage": 1.09, "request": 0.0, "retrieval_observed": 0.0,
                       "retrieval_stress": 5.83, "it_monitoring": null},
    "transition_cost": 25.0, "transition_cost_existing": 25.0,
    "savings_monthly": {"low": 16.08, "high": 21.91},
    "savings_yearly": {"low": 192.96, "high": 262.92},
    "breakeven_months": 1.6,
    "risks": [],
    "assumptions": {"full_retrievals_per_year": 1, "retrieval_assumption": "none_observed",
      "padding_method": "avg_size_estimate", "object_count_method": "remainder_derived", "lifecycle_small_object_override": false}
  }, {
    "policy": "DIRECT_WRITE_DEEP_ARCHIVE", "status": "ok",
    "ladder_rank": null, "ladder_group": "direct_write",
    "recommended": true, "recommendation_reason": "no reads observed in 62 days; writing new objects directly to DEEP_ARCHIVE avoids the transition fee and the 30-day Standard stay",
    "tolerated_full_retrievals_per_year": 3.76,
    "monthly_costs": {"storage": 1.09, "request": 0.017, "retrieval_observed": 0.0,
                       "retrieval_stress": 5.83, "it_monitoring": null},
    "transition_cost": 0.0, "transition_cost_new_objects": 0.0,
    "backlog_scenario": "DEEP_ARCHIVE",
    "savings_monthly_new_objects": 21.89,
    "savings_monthly": {"low": 16.06, "high": 21.89},
    "savings_yearly": {"low": 192.72, "high": 262.68},
    "breakeven_months": null,
    "risks": [{"code": "DIRECT_WRITE_UNREADABLE_WITHOUT_RESTORE",
      "reason": "objects written straight to DEEP_ARCHIVE are not readable without a RestoreObject call", "value": null}],
    "assumptions": {"full_retrievals_per_year": 1, "retrieval_assumption": "none_observed",
      "padding_method": "avg_size_estimate", "object_count_method": "remainder_derived"}
  }],
  "recommendation": {
    "policy": "DIRECT_WRITE_DEEP_ARCHIVE",
    "reason": "no reads observed in 62 days; writing new objects directly to DEEP_ARCHIVE avoids the transition fee and the 30-day Standard stay"
  },
  "pattern": {
    "history": {"months": ["2026-08"], "data_write": [200], "data_read": [0], "config": [340],
      "retrieval_gb": [0.0], "egress_gb": [0.0], "stored_gb": [1000.0]},
    "peak_data_read_month": null, "zero_read_months": 1,
    "implied_object_lifetime_days": 75000.0, "growth_pct_over_window": null,
    "small_object_share_estimate": null, "egress_share_of_bucket": 0.0,
    "pattern_label": "write_once_cold", "write_only": true,
    "pattern_summary": "No reads observed in any covered month; objects are written once and never read back."
  },
  "unmapped_usage_types": []
}
```
`telemetry_status`: `"usable"` | `"unknown"`. `confidence`: `low`|`medium`|
`high` (§2.1). `scenarios[].status`: `"ok"` | `"not_beneficial"` |
`"pricing_unavailable"` | `"size_unavailable"` (§3.4 — `stored_gb_by_class`
has no CloudWatch datapoint at all) | `"size_distribution_unavailable"`
(§2.6 — transition-based scenario only, `avg_object_bytes < 131072` and no
override). Transition-based scenarios' `assumptions` carry
`size_distribution`: `"unknown_assumed_all_eligible"` (when priced) or
unset (when `status == "size_distribution_unavailable"`, since nothing
was priced to assume anything about). `scenarios[].ladder_group`: `"main"` |
`"side"` | `"chain"` | `"direct_write"` (§4.6, §4.9); `ladder_rank`: `1`–`4`
or `null`. `scenarios[].assumptions` additionally carries `padding_method`
(`"observed_overhead"` | `"avg_size_estimate"` | `None`, §4.1) and
`retrieval_assumption` (`"egress_lower_bound"` | `"none_observed"` | unset
when the class charges retrieval directly, §4.3) alongside
`object_count_method` (§2.4) whenever the scenario's math depends on an
estimated rather than exact count. Every scenario carries `recommended`
(bool) and `recommendation_reason` (string); `tolerated_full_retrievals_per_year`
(float or `null`, §4.9). Transition-based scenarios carry
`transition_cost_existing` (equal to `transition_cost`); `direct_write`
scenarios instead carry `transition_cost_new_objects: 0.0`,
`backlog_scenario` (the `policy` string of the matching transition
candidate), and `savings_monthly_new_objects` (float or `null`, §4.9).
`pattern.pattern_label`: `unknown` | `write_once_cold` | `log_sink` |
`periodic_bursts` | `actively_read` | `steady_low_reads` (§3.5);
`pattern.write_only` (bool, §3.5). `ladder_group: "chain"` is a **reserved
enum value with no V1 candidate** (§4.6, §10 item 18) — no `scenarios[]`
entry carries it until chains return. Transition-based (non-`direct_write`)
scenarios' `assumptions` additionally carry `lifecycle_small_object_override`
(bool, default `false`, §2.6) — absent on `direct_write` scenarios, which
are exempt from the lifecycle size floor.

**`signals.window_totals`** (N9) — the raw `window_*_total` values §3.3
promises are retained in the result: `covered_days`, `tier1` (split by
family — `data_write`/`list`/`config`/`tier1_unsplit`, §2.3), `tier2`
(split by family — `data_read`/`config`/`tier2_unsplit`), `config_requests`
(the combined config-family total, excluded from every access signal,
§2.3/§4.2), `retrieval_gb` and `restore_requests` (both keyed by
`[class][speed]`), and `egress_gb`. This is what finding text draws on to
state an observed total ("3,410 PUT/COPY/POST/LIST requests over 90
days") — §3.4's
`monthly_*` signals are the normalised decision inputs, `window_totals` is
the raw evidence.

**Bucket-level `recommendation`** (N9) — `{policy: str | null, reason:
str}`, always present at the top level (unlike `scenarios[]`, which may be
empty). `policy` is `null` exactly when no scenario has `recommended:
true` (§4.9 phase 1 rows 1/6, or phase 2's dominated-with-no-write-only
branch); otherwise it is that scenario's `policy` string. This is where
rows 1, 6, and phase 2's "no change" branch — none of which have a
`recommended: true` scenario to attach a reason to — report their reason
text, since the frozen contract otherwise has nowhere for a "no
recommendation, and here's why" statement to live. **`scenarios[].recommended`
is retained for convenience** (so a consumer iterating `scenarios[]`
doesn't have to cross-reference the top-level field), but it **must always
agree** with `recommendation.policy` — exactly the scenario named there (or
no scenario, if `policy` is `null`) carries `recommended: true`. The two
are never allowed to disagree; this is a single fact represented in two
places for two different access patterns, not two independent facts.</content>
