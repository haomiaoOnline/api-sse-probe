# v2.3.0: deterministic assertions and accuracy contract

## Scope and invariant

This branch **does not call an LLM in the assertion execution path**. The frozen SSE evidence and a versioned deterministic rule compile produce the same judgement and trace. AI may assist with drafting or editing a rule *before approval*, but cannot influence any online PASS/FAIL decision. Existing Chinese nine-column cases from Tencent Docs, Feishu Sheets and CSV remain unchanged.

```
source rows -> normalize / preflight -> compile each positive rule -> durable SSE capture
-> offline parse verified response data -> typed rule evaluation -> PASS / FAIL
                                                   |  insufficient protocol evidence
                                                   +-> NOT_EVALUATED marker (keep v2 status)
-> private evidence report and approved Feishu summary
```

`RULE_ENGINE_VERSION=deterministic-1`. A rule compiles to `{version, kind, body, sha256}` using canonical JSON and SHA256; the hash is stable for exactly the same rule and does not include user requests, response contents or credentials. The private per-case `assertion_trace` records rule ordinal, version, hash, kind, source scope, optional business field path and boolean verdict. The private `assertion_evaluation_status` is `EVALUATED`, `INVALID_RULE`, or `NOT_EVALUATED` (not enough protocol evidence). Existing public report and API statuses stay PASS/FAIL/ERROR, so consumers do not need an immediate fourth enum.

- `public`: HTTP status, nonempty assistant text, structured followups, title, sdk, deeplink, actual skillId, explicit travel-advice wording.
- `explicit_natural` and `text_expression`: strictly parsed quoted AND/OR conditions on assistant text or title. The requested content is not considered response evidence.
- `json_expression`: `all`, `any`, `not`, `same_frame` and typed `equals`, `contains`, `prefix`, `exists`, `nonempty`, `in`, `gte`, `lte`, `count_gte`. A same-frame conjunction cannot be satisfied by combining business fields from separate messages.
- `field`, `video_compound` and `title_content_compound`: specific real assistant data, not top-level request metadata.
- `literal`: a backward-compatible explicitly recognized short keyword on the sanitized assistant *business payload* (potentially multiple fields). It proves byte-substring existence, never semantic correctness. For accuracy-sensitive new cases prefer an explicit text/title/data scoped predicate.
- Malformed or vague rules are rejected at **case import before network POST**. If a caller bypasses preflight and invokes a low-level evaluator, an unrecognized rule is `INVALID_RULE` with a failed verdict; it can never silently PASS, even if other conditions satisfy `match_mode=any`.
- Forbidden words remain literal checks, not positive typed assertions. Failure to receive a complete SSE response cannot become a successful assertion; `NOT_EVALUATED` keeps old protocol FAIL/ERROR semantics and does not pretend a semantic verdict was reached.

## Accuracy measurement and evidence

A closed synthetic golden set includes **20 manually labelled cases** (expected PASS or FAIL): assistant-text vs metadata isolation, title exactness, empty followups, AND/OR grouping, cross-frame same-frame rejection, numeric number-vs-string and strict 500 threshold, negative condition, forbidden keyword, and missing text. All 20 compared results match the labels, yielding TP/TN-based accuracy 20/20 and false-positive/false-negative counts 0/0 **only for that synthetic set**. Re-running identical inputs yields identical rule digests and traces. Additional tests cover malformed/vague rule rejection and transport `NOT_EVALUATED` behavior.

Two private historical real runs were replayed *without network requests or modifying historical files* using `RunStore.evidence` (verified bytes) and `judge_case`: 19+19 cases, **38/38 status+reason pairs identical** to the saved v2.2.0 evaluations. Historical run A remains 18 PASS/1 FAIL; run B remains 14 PASS/4 FAIL/1 ERROR. This verifies compatibility, not semantic correctness of the historical labels. No raw SSE, utterance, device ID or user secret is included in this document or package.

**No claim of 100% real-world semantic accuracy.** An explicit `contains` rule guarantees substring truth, not whether a flight really meets a budget; business claims require observable structured price/availability fields or an explicitly approved predicate. Production accuracy targets require a larger independently human-labelled corpus and metrics covering not only accuracy, false positives and false negatives, but also **coverage** (fraction of business scenarios with auditable predicates). Rejecting all difficult cases may never count as improved quality.

## Compatibility/release gates

- v2.2.0 historical frozen cases and two historical evaluations are untouched; no schema rewrite, API POST, new notification, or secret disclosure.
- Python 3.9+ standard library, existing positional evaluation return values and PASS/FAIL/ERROR report API preserved.
- Source preflight and direct legacy evaluator are both fail-closed on unrecognized positive rule syntax.
- Synthetic golden, all existing unit tests, cross-field isolation, stored-response replay, and independent installed-ZIP tests must pass before considering merge to `main`.
- Windows real hardware/power-loss validation is outside the currently approved scope.

## Formal release summary

After golden and private historical verification, 296 offline tests (including installed ZIP) passed. This release changes rule compilation, deterministic execution and private traceability only; it does not claim a real-time production SSE re-probe was made. The prior verified Feishu/Tencent case import contracts are preserved. `v2.2.0` remains a rollback point.