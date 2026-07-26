# PP-COST — Cost and projection

The portal reports consumption but no prices. Everything here turns kWh into money from a
tariff the user supplies.

### PP-COST-001 — Only billable readings are priced
`W` and `E` count; `V` and unknown flags do not. Non-billable energy is reported separately
rather than dropped silently.

*Why:* provisional values are replaced later. Pricing them invents consumption the supplier
never invoices, and the discrepancy would be invisible.

### PP-COST-002 — Money rounds half-up at the cent
Not banker's rounding.

*Why:* it is how invoices round. Half-to-even would disagree with the supplier's own figure
by a cent at a time.

### PP-COST-003 — A total is the sum of its already-rounded parts
`total_eur == energy_eur + base_eur`, each rounded first.

*Why:* a bill whose parts do not add up to its total is worse than one that rounds a cent.

### PP-COST-004 — The standing charge is charged pro rata
Spread across the covered period against a nominal 365-day year.

*Why:* a partial period must not be charged a full year's Grundpreis.

### PP-COST-005 — Projection extrapolates per covered hour
Not per calendar day.

*Why:* it handles a half-finished final day, which would otherwise drag the average down,
and it fills gaps at the observed rate instead of counting them as zero consumption.

### PP-COST-006 — A billing year may start on any date, and must exist every year
`(month, day)`, defaulting to 1 January. 29 February is rejected.

*Why:* supply contracts rarely align to the calendar year. A start date that does not exist
in every year has no successor.

### PP-COST-007 — Readings outside the billing year are ignored
Only readings within the current billing year contribute.

*Why:* the projection answers "what will this billing year cost", not "what has this meter
ever recorded".

### PP-COST-008 — Without a configured advance there is no settlement figure
`settlement_eur` is `None` rather than zero.

*Why:* zero would read as "nothing to pay", which is a claim. `None` says the question
cannot be answered yet.

### PP-COST-009 — Negative prices are rejected at construction
`Tariff` raises rather than accepting them.

*Why:* a negative price silently inverts every downstream figure.
