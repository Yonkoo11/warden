# Probe

Measurements of the sponsor surface, made before feature code. A result here outranks a doc page; anything unmeasured is not claimed in the README.

| file | question | result |
|---|---|---|
| `poll_print.py` | does a BSC stock token move on-chain after an earnings print? Polled the public endpoints for APLDon (7 Oct) and PEPon (8 Oct) every 5 minutes | rows in `prints.jsonl`; the hosted service superseded this poller |
| `swap_once.py` | does `baw market-order swap` reach FINISHED for a paired wallet? | yes, three orders on 2026-10-05, raw responses in `swaps.jsonl` |
| K-line time ranges | which `startTime`/`endTime` forms the public K-line endpoint accepts | none; every form tried returned code 000002, so only `limit` is used |

Rules: never guess an endpoint or schema; one observation is one observation; a local test without the sponsor's backend proves nothing.
