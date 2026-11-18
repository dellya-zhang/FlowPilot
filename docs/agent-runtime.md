# Agent runtime

## Contract

Marketing is a task, support is a conversation. SQL persists business state; LangGraph owns a single execution. A generation restart reruns the plan, not a node-level checkpoint.

- Plan: constrained candidate generation and approval; preview only.
- Pilot/Full: verify frozen snapshot digest, validate audience, assemble approved copy, terminal compliance, assemble CSV. A/B create writes per-arm CSV to configured object storage before committing its binding. A failed transaction may leave unreferenced objects; no referenced partial publication. No actual marketing send.
- Support: deterministic intent screening, conditional graph, read-only retrieval, priority rules, guarded confirmation, SQL message/audit transaction. Optional model selects a read tool and then evidence IDs, at most two calls, shared provider quota; the model cannot choose a write action. Cloud selection is explicit opt-in. The graph uses extractive answers, not free-form LLM composition. H5 is served by the complete backend; static Pages links to the existing simulation.
- A/B settlement uses descriptive ratios, not significance; legacy promotion remains compatible. Full excludes both arms. PII pattern checks are illustrative, not complete DLP.

## Run

See README for startup. Navigate to / for marketing and /support.html for customer-facing chat. Run pytest -q for unit and integration tests; inspect /docs for endpoints. New audit table is additive via create_all.

## Remaining work

Real customer sources, production identity/tenancy, external order and marketing integrations, node checkpoint/resume, full compliance retry/regeneration and infrastructure load verification are not implemented by this change. No enterprise source, internal URL or user data is included.

Plan model text compliance retry: at most two re-generations, with violation categories rather than the rejected text in the retry prompt. Fixed operator reference text is blocked without rewriting. The materialization graph does not regenerate approved copy. Browser CI runs a real Mock API and Chromium, with screenshots as CI artifacts instead of updating checked-in screenshots before verification.
