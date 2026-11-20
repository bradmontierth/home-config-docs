# First circuit-pattern assessment — 2026-09-27

Evidence: 34,183 stored IotaWatt rows covering Sep 25 09:14:40 through Sep 27
09:14:40 America/Denver; approximately the first five minutes of Vue capture
starting Sep 27 10:06:45. Recordings do not overlap. This is comparison of load
levels, variability and historical operating ranges, not time-aligned correlation
or accuracy calibration. Absolute Vue watts are used only for pattern comparison;
raw logged values and firmware remain signed and unscaled.

| Input | Observed magnitude | Candidate | Confidence |
| --- | --- | --- | --- |
| 1 | ~153 W | Fridge | Confirmed by user; CT never moved |
| 16 | ~115 W baseline, brief rise | Upstairs Beds | Medium; legacy stable ~119 W baseline, historical 106–252 W |
| 9 | ~79 W, very stable | Utility Closet | Medium; legacy ~74.5 W, stable |
| 13 | ~240 W, variable | Office | Medium; legacy ~211 W before removal, variable, 50–379 W range |
| 7 or 10 | ~8.2 / 6.3 W | Furnace standby | Low; legacy ~7.3 W; cannot select uniquely |
| 12 or 8 | ~4.7 / 3.1 W | Water heater standby | Low; legacy ~3.9 W; other idle loads also plausible |
| 2, 3, 4, 5, 11 | ~0.5–1.1 W | Idle/near measurement floor | No unique identity |
| 6, 14, 15 | ~58 / 37 / 62 W | Unassigned active loads | Could be previously unmonitored branches |

At idle, multiple large appliances have overlapping small standby draws. User
plans to run dishwasher, dryer, oven, EV, AC and Claire mini-split separately to
identify those inputs. Each test should have a timestamp and a clear normal-control
change, preferably a second observed transition. Do not rapidly cycle compressors.
A response identifies the circuit feeding the appliance; it does not prove that
nothing else shares it. Long-term patterns and manual tests must confirm candidates
before renaming deployed entities or rerouting automation decisions.

## Planned scaling

Carry forward the requested 2x **power and energy** scaling for single-leg dryer,
range/oven, EV, AC and Claire mini-split measurements after confirming their mapped
inputs and one-leg measurement arrangement. Dishwasher and fridge remain 1x.
Do not multiply measured CT amperage by two. If both legs are independently
measured, sum their watts instead of doubling each. Mixed 120/240 V loads can have
unequal leg loads, so a one-leg doubled estimate is not necessarily exact.

No firmware, calibration, HA labels or automation changes in this assessment.
`circuit-map.csv` now separates candidate names/confidence from confirmed labels.
Detailed numeric summaries: `identification-summary-20260927.json`.
