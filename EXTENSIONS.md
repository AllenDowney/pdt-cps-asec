# Extensions

Every key under `extensions` in this repo's records, with a reason (STANDARD.md §4.4). `pdt check` fails a key that isn't listed here (C10).

| Key | What it holds | Source | Why it's an extension |
|---|---|---|---|
| `cps.item` | The questionnaire item number the data dictionary gives in a label ("Item 18h - Educational attainment" for A_HGA) | the data dictionary | The standard has no field for a publisher's item reference; the NSFG keeps its own as `nsfg.item` |

An extension that a second dataset also needs is a proposal for the standard: `cps.item` and `nsfg.item` are the same thing.
