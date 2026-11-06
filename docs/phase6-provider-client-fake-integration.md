# Phase 6 Fake-Client Integration

Status: **PASS for offline fake-client orchestration only**. No provider SDK client, credentials, network access, JEV, or answer-data egress is present in this integration.

## Interface and records

`src/evidence_layer/provider_execution.py` defines a provider-neutral async `ProviderClient` protocol and fakeable execution types:

- `ProviderJob`: run/case/arm, provider, requested model, pricing snapshot, client, and caller-assigned `job_ordinal`.
- `ProviderReply`: response payload and success/refusal/partial outcome; its body is consumed in memory and never copied into an attempt record.
- `ProviderAttempt`: attempt ID/ordinal/index, requested model ID, exact returned model ID or `None`, outcome, safe error category, usage-parse status, `UsageEvent`, and recomputed cost.

`run_provider_job` invokes one call per attempt and invokes the previously reviewed parser on complete response payloads. `run_provider_batch` sorts jobs by their frozen `job_ordinal` before starting concurrent work. `attempt_ordinal = job_ordinal * max_attempts + attempt_index + 1`; ordinals are unique, stable under completion-order changes, and may contain gaps where attempts were unused.

Attempt IDs incorporate run, case, arm, provider, requested model, and attempt number. The returned model field is copied only from the provider response. If absent, `returned_model_id` remains `None`; it is never filled with the requested value. A missing or mismatched model identity marks the attempt failed for pooling, while parsed usage remains available to account for possible incurred cost.

## Retry, rejection, and partial-response behavior

- Each attempt has `calls=1`; initial attempt has `retries=0`; each retry attempt has `retries=1`.
- Only `timeout` is retried by the fake harness. Rejection, partial response, unknown transport exception, and usage invariant failures terminate the job by default.
- Every failed network attempt returns a Usage record with unknown/null counts and cost; no failure becomes zero-cost.
- Partial responses are not parsed as completed usage. Without terminal usage fields, the attempt remains unknown.
- Refusal outcomes can still retain parsed usage and cost because the provider may bill the completed refusal.
- Provider exception strings, payload content, prompts, answers, and secrets are not copied into `ProviderAttempt`; only enumerated safe categories are stored.

## Tests

`tests/test_provider_execution.py` uses fake clients only and checks:

- successful response parsing/cost and that answer content is absent from returned records
- request rejection with null usage/cost
- timeout followed by an explicitly counted retry
- partial response handling
- returned model missing/mismatch without requested-ID substitution
- stable unique ordinals under concurrent jobs with reverse completion order
- usage invariant failure recorded as a safe failure, not success/zero cost

The composed 40-case/three-virtual-model pipeline is documented in `docs/phase6-offline-fake-e2e-pipeline.md` and exercised by `tests/test_phase6_fake_e2e.py`.

The parser tests remain in `tests/test_provider_usage_adapters.py`; cost policy tests remain in `tests/test_usage_measurement.py`.

## Boundary before real SDK wiring

The fake harness proves orchestration semantics, not provider interoperability. Real SDK wiring still requires explicit provider/data-egress authorization, secret-manager setup, returned-model/usage field validation, retry disabling at transport/SDK level, and a signed-off request/data-retention profile. DeepSeek cache-write billing applicability remains unknown. Model Matrix and Pricing Registry remain unfrozen/unapproved; external evaluation remains unauthorized.
