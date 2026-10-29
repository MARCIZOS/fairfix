# FairFix evaluation results

Run: 2026-10-28 22:01 · cases: 12 (0 real, 12 mock) · mode: Nemotron mapping and messages + reading photos

These are small-sample results for the listed cases only, not evidence of broad real-world reliability.

## Release gate: PASS

- Unsupported flags after correction (Needs confirmation where expected differs): **0**
- Accusatory wording in labels, next steps or messages: **0**


## Accuracy (raw counts)

| Measure | After correction | Straight from the photo |
| --- | --- | --- |
| Line mapped to the expected job | 57 of 57 (100%) | 50 of 51 (98%) |
| Schedule category | 57 of 57 (100%) | 50 of 51 (98%) |
| Price category | 57 of 57 (100%) | 51 of 51 (100%) |
| Coverage category | 57 of 57 (100%) | 50 of 51 (98%) |
| Authorization category | 57 of 57 (100%) | 50 of 51 (98%) |
| Arithmetic category | 57 of 57 (100%) | 49 of 51 (96%) |
| Bill-level arithmetic | 24 of 24 (100%) | 18 of 20 (90%) |

**Fields read correctly from the photos** (lines, amounts, quantities, part numbers, totals, GST basis, personal data removed): 166 of 172 (97%)

Flags raised straight from the photo that wouldn't be raised after correction (the correction screen is there to catch these):

- hard_thermal_receipt bill check 1: "Subtotal differs from the sum of lines by ₹16"
- hard_thermal_receipt bill check 2: "Grand total differs from the bill's own lines and taxes by ₹1,152.19"

## Every wrong verdict after correction (inspect each by hand)

- None

## Every wrong verdict straight from the photo

- hard_damaged_photo line 2 (AIR CLNR ELEMENT) arithmetic: expected unable_to_verify, got supported - "Line arithmetic adds up"
- hard_damaged_photo line 3 (PERIODIC SERVICE LABOUR) arithmetic: expected supported, got unable_to_verify - "Line amount not readable"
- hard_hindi_items line 5 (गुल्लाख): job expected washing, got unrecognised
- hard_hindi_items line 5 (गुल्लाख) schedule: expected not_applicable, got unable_to_verify - "Item not recognised: ask the advisor what this item is"
- hard_hindi_items line 5 (गुल्लाख) coverage: expected not_applicable, got unable_to_verify - "Item not recognised: ask the advisor what this item is"
- hard_hindi_items line 5 (गुल्लाख) authorization: expected not_applicable, got unable_to_verify - "Item not recognised: ask the advisor what this item is"
- hard_thermal_receipt bill check 1: expected not_applicable, got needs_confirmation - "Subtotal differs from the sum of lines by ₹16" (sum of lines ₹1,246; printed subtotal ₹1,230)
- hard_thermal_receipt bill check 2: expected supported, got needs_confirmation - "Grand total differs from the bill's own lines and taxes by ₹1,152.19" (₹1,246 − discount ₹0 + round-off ₹0 (prices include tax) = ₹1,246; printed total ₹93.81)

## Reading misses

- hard_damaged_photo: line 2 (AIR CLNR ELEMENT) amount: expected None, got 400.0 (printed value is hidden: it should be null, not guessed)
- hard_damaged_photo: line 3 (PERIODIC SERVICE LABOUR) amount: expected 400, got None
- hard_dealer_gst_invoice: subtotal: expected 1265.0, got 1072.03
- hard_thermal_receipt: line 1 (ENG OIL 900ML) amount: expected 340, got 348.0
- hard_thermal_receipt: line 3 (AIR FLTR ELEMENT) amount: expected 310, got 318.0
- hard_thermal_receipt: grand_total: expected 1230.0, got 93.81

## Model calls (from logs/calls.jsonl, this run)

| Step | Calls | OK first try | Failed first try | OK after retry | Still unusable | Busy retries | Timeouts | Provider errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extract | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| message | 17 | 17 | 0 | 0 | 0 | 0 | 0 | 0 |
| normalise | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |

Time per audit (reading the photo included): median 3.8 s, slowest 6.8 s. Price search was off.
