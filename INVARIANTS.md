# Invariants

Rules the system must never break. Each one states the rule, why it exists, and how it is enforced
in code — not in memory. A rule with no enforcement is a wish.

| # | invariant | why | enforced by |
|---|---|---|---|
| 1 | <e.g. no confidential value is ever materialised in plaintext — not in storage, not in an event, not in a revert string> | <the property that breaks otherwise> | `<file:function>` / `<test>` |
| 2 | <e.g. every output row records which backend tier produced it> | a demo on a degraded tier must be visibly degraded, never silently | `<file>` |
| 3 | | | |

## Discovered the hard way

<Each line: what broke, what it cost, and the invariant that now prevents it.>
