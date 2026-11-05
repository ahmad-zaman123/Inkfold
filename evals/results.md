# Extraction accuracy

Measured on 2026-11-05 with `Backend/scripts/eval_accuracy.py`, on the 24 made-up documents in `evals/cases/`.
Each one went through the same code as an upload: Gemini reads it, then Inkfold checks and cleans every field.
Models that answered: `gemini-3.1-flash-lite` (14), `gemini-3.5-flash-lite` (10).

## Summary

| Measure | Result |
| --- | --- |
| Fields read correctly, where the document has the value | **100%** (210 of 210) |
| Fields correctly left blank, where the document doesn't have them | 100% (102 of 102) |
| All fields, both kinds together | 100% (312 of 312) |
| Wrong or made-up values that the review screen flagged | n/a (0 of 0) |
| Phone numbers with no country code, flagged as they should be | 2 of 2 |
| Correct values flagged anyway (extra checking for the reviewer) | 0 |
| Document type recognised | 96% (23 of 24) |
| Invoices whose line items add up to the total | 100% (8 of 8) |

A value is flagged when one of Inkfold's checks fails or Gemini's confidence is under 70%.
Missed values show up as empty fields, which the reviewer sees too.

## By document type

| Type | Documents | Fields read correctly | Left blank correctly |
| --- | --- | --- | --- |
| Business cards | 6 | 100% | 100% |
| Invoices | 8 | 100% | 100% |
| Lead forms | 6 | 100% | 100% |
| Emails and notes | 4 | 100% | 100% |

## By field

| Field | Correct | Of |
| --- | --- | --- |
| First name | 100% | 24 |
| Last name | 100% | 24 |
| Email | 100% | 24 |
| Phone | 100% | 24 |
| Job title | 100% | 24 |
| Company | 100% | 24 |
| Website | 100% | 24 |
| Address | 100% | 24 |
| Invoice number | 100% | 24 |
| Issue date | 100% | 24 |
| Due date | 100% | 24 |
| Currency | 100% | 24 |
| Total | 100% | 24 |

## Every mistake

None.

Wrong document type: email-03
