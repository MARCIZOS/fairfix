# FairFix evaluation results

Run: 2026-10-27 20:09 · cases: 3 (0 real, 3 mock) · mode: rules + alias mapping, no network

These are small-sample results for the listed cases only, not evidence of broad real-world reliability.

## Release gate: PASS

- Unsupported flags (Needs confirmation where expected differs): **0**
- Accusatory wording in labels, next steps or messages: **0**


## Accuracy (raw counts)

| Measure | Result |
| --- | --- |
| Line mapped to the expected job | 12 of 12 (100%) |
| Schedule category | 12 of 12 (100%) |
| Price category | 12 of 12 (100%) |
| Coverage category | 12 of 12 (100%) |
| Authorization category | 12 of 12 (100%) |
| Arithmetic category | 12 of 12 (100%) |
| Bill-level arithmetic | 6 of 6 (100%) |

## Every wrong verdict (inspect each by hand)

- None

## Model calls (from logs/calls.jsonl, this run)

| Step | Calls | OK first try | Failed first try | OK after retry | Still unusable | Timeouts | Provider errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| normalise | 1 | 0 | 0 | 0 | 0 | 0 | 1 |

Latency per audit: median 0.00 s, slowest 0.00 s (search off; add price search time separately).
