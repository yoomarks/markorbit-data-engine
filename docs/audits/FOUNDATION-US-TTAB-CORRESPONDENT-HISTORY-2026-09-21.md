# Foundation US TTAB correspondent historical lookup — 2026-09-21

## Outcome

The US TTAB party-correspondent historical handled-mark seam is production accepted and READY.

- route: `GET /api/v1/us/correspondents/by-name/ttab-history`;
- READY: `US_TTAB_CORRESPONDENT_MARK_HISTORY_READY_V1`;
- serving generation: `1`;
- accepted projection rows: `2,070,969`;
- unique relationships: `1,577,684`;
- normalized correspondent names: `182,377`;
- serials: `723,093`;
- registrations: `260,710`.

## Production acceptance

Final verification-only receipt:
`D:\yoomarks\governed-plans\788\us-ttab-correspondent-history-production-r4.receipt.json`

- receipt SHA-256: `40fe93fe6fae089d345411516dccfaeedcd88448125add9daa5494c15554d840`;
- implementation SHA: `5254be094cd9780ff935ea37e1acb2737a51e6c7`;
- plan SHA: `8a3bc58eae3314a3482a257f76008792a63fee7a88a84d1d4b2f6a413d897791`;
- completeness: exact count + relationship/name counts + binding sum/xor + 200/0 deterministic sample mismatch;
- first-page p95: `56.086 ms`;
- subsequent-page p95: `53.57 ms`;
- SLO: `<= 400 ms`;
- runtime smoke: `10` results.

## Semantic boundary

The projection uses direct official USPTO TTAB party-correspondent observations linked only to the same party's TTAB trademark properties. It excludes TTAB interlocutory/staff attorneys, does not substitute Assignment party names, and does not claim cross-source identity, continuing representation/customer relationship, ownership/title, proceeding outcome, or substantive legal rights.
