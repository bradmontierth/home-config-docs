# Manual load sequence — 2026-09-27

User reported dishwasher wake-up (not full cycle), then stove on about 5 seconds,
dryer on about 10 seconds, washer started and left running, and furnace fan left
running. Exact action times requested for the sustained/ambiguous changes.
Recorded times below are America/Denver (MDT, UTC-6). Magnitudes are raw single-CT
watts, without 2x correction or calibration to IotaWatt.

## Strong matches

| Input | Event | Interpretation |
| --- | --- | --- |
| 4 | 10:13:58–10:14:04, seven samples; ~652 W plateau, 669 W peak | Stove/range, consistent with duration and order |
| 2 | 10:14:33–10:14:42, ten samples; ~274 W motor plateau followed by ~3269 W peak | Dryer, consistent with duration/order and motor then heater |
| 7 | 10:16:56, ~8 W idle → sustained ~637 W, brief startup peak ~1275 W | Furnace fan; also matches historical operating level |

Input 2 and 4 power/energy multipliers recorded as 2, input 7 as 1. Corrected
approximate peaks would be 6.54 kW for dryer and 1.34 kW for the tested stove
load; current is not doubled. These are not verified whole-appliance totals for
mixed 120/240 V loads. Actual firmware retains signed, unscaled numbered readings.

## Unresolved changes

- Inputs 6 and 14 rise together around 10:13:05–06. Input 6 rises from ~58 W,
  peaks ~723 W during the observation, and remains roughly 150–200 W with pulses;
  input 14 rises from ~37 W to ~80 W, later ~63 W. Could include room lighting /
  other shared loads, not enough evidence to assign dishwasher.
- Input 16: 10:18:04–10:18:46, ~1373 W median, ~2162 W peak, then ~166 W.
  This makes the prior Upstairs Beds candidate less secure; don't force a label.
- Input 8: 10:18:57, ~3 W to ~200 W sustained, later ~189 W. Could be washer or
  water heater responding to hot-water demand. Historical gas water-heater load
  reached ~230 W, so power level alone is insufficient.
- Input 9: 10:20:01, ~79 W to ~533 W briefly, later ~111 W. Prior Utility Closet
  candidate remains unconfirmed; this could reveal shared loads or another circuit.
- No unique small, temporary dishwasher wake-up signature identified yet. A full
  dishwasher cycle or a timed repeat would give stronger evidence.

Updated `circuit-map.csv` with the strong matches and revised candidate confidence.
No firmware, HA entity names, calibration or automation source changed in this
analysis. The one-second raw capture continues.

## Follow-up through 12:31 MDT

About 8,650 one-second snapshots inspected from 10:06:45 through 12:31:05.
Capture is still connected; one disconnected snapshot in the inspected window,
maximum snapshot timestamp gap ~1.02 seconds (snapshot cadence is not itself a
per-sensor freshness guarantee).

- **Input 8 — strong water-heater candidate.** Active 10:18:57–10:36:27,
  median 187.7 W, ~3 W standby before/after. Nearly flat after startup.
  Five stored legacy WaterHeater cycles have medians 171.9–180.8 W and durations
  about 11–38 minutes. Shape/duration support the match; magnitude is not used
  to calibrate either instrument. Washer-triggered hot-water demand is a possible
  explanation, not an observed fact.
- **Input 12 — new Claire Mini candidate, medium-high confidence.** First cycle
  11:06:12–11:32:07, another starts 12:05:36 and continues at cutoff. Peaks
  88.8/84.1 raw W, taper to ~33–36 raw W. Applying the proposed single-leg 2x
  interpretation offline gives ~168–178 W peaks and ~66–72 W settled load,
  resembling historical ClaireMini cycles (~153–176 W peaks and ~64–76 W
  medians in several examples). Similarity is not unique; controlled test needed.
  Withdraw previous water-heater candidate for input 12.
- **Input 6 — washer/shared-circuit candidate only.** Variable elevated load
  from 10:13:06 until about 11:25, then returns to ~63 W background. Cycle-like
  behavior supports investigation, but onset is BEFORE stove/dryer tests and
  conflicts with the reported washer-start order. Do not resolve by assumption.
- **Input 10 remains unidentified.** ~6 W to ~135 W at 10:26; continuous through
  12:31. No unique evidence to identify it as washer or another appliance.
- Furnace input 7 returned from ~635 W to ~8 W at 10:24:39.
- No new identifying high-power activation on inputs 3, 5, 11; dishwasher,
  EV and AC remain unresolved. Office/closet/bedroom candidates unconfirmed.

Candidate updates saved to circuit-map.csv only. No flash, firmware multipliers,
HA/Node-RED changes or service restart performed.

## User context: shared washer circuit and UPS transfer

User confirms washer shares a circuit with the bidet, and reports transferring
UPS input from an LFP battery pack to the wall before departure. UPS supplies
N100, GX10, Plex Pi and UDMP. Exact transfer time/outlet circuit not supplied.

Input 6 is now a medium-confidence Washer + Bidet candidate: background before
activity ~58 W / 0.65 A and after ~62 W / 0.68 A, with variable intervening load.
The shared load can explain background draw and potentially early activity, but
does not prove that 10:13 was washer start or that the background is the bidet.
Input 10 is a medium-high-confidence UPS / compute/network candidate: ~6 W /
0.10 A before 10:26, then ~135 W / 1.51 A, with sustained ~130-140 W afterward.
This matches an input power-source transfer in shape; wall reading may include
UPS losses and battery charging, and is not a direct sum of equipment power.
Saved as candidates only; firmware and HA unchanged. Capture inspected through
12:36:45 MDT.

User confirms 10:26 MDT was immediately before departure and fits the UPS
transfer. Promoted input 10 to high-confidence UPS / Compute and Network label
in the mapping table, power/energy multiplier 1. Phase/polarity remain unverified.
Firmware entity names and automation sources unchanged.

## AC start reported by user — 15:04:56 MDT

Input 3 rose from ~1 W to ~1.5 kW single-leg at 15:04:56. Input 7
(furnace) followed at 15:04:59, settling near 1132 W versus ~635 W for
the earlier fan-only test. Input 3 median 15:05:05–15:05:26 was 1505.74 W
and 15.91 A. Proposed 2x power is ~3.01 kW, plus ~1.13 kW blower.
Comparing 15:04:20–49 against 15:05:05–26, summed mains power magnitudes
rose ~4.01 kW (~1.56 to ~5.58 kW), broadly supporting the combined loads.
This is a rough step cross-check, not calibrated/phase-verified whole-home power;
other loads can change between windows. Assign input 3 AC with high confidence,
power/energy multiplier 2 (current not doubled). No firmware or HA changes.

## Microwave manual start — 15:31:20 MDT

User reports starting microwave. Input 5 rises from <1 W to ~1690 W,
with no comparable step on the other unidentified inputs. Assigned Microwave
with high confidence, linked to user-reported breaker 13; multiplier 1.
Firmware and HA unchanged; raw logging continues.

## Dishwasher start reported around 15:54 MDT — unresolved

Capture inspected through 15:56:00 MDT. No distinct sustained new branch
load identified after user reports dishwasher cycle start. Input 5 has another
~1690 W pulse around 15:53:10–15:53:39, resembling earlier microwave event;
asked whether microwave was used again and whether dishwasher is audibly
filling/washing. Do not label a CT or infer absent CT from this negative evidence.
No firmware/HA changes.

## EV start — 15:56:49 MDT

User reports plugging EV in, relay click, charger showing 6 A. Input 11 rises
from ~1 W to ~714 W on measured leg, CT reports ~6.00 A. Assign EV Charger
with high confidence, proposed power/energy multiplier 2 (~1.43 kW), current
unchanged. Linked to reported bottom-right 50 A breaker starting at 28.
User also notes dishwasher may have been skipped during CT installation;
coverage still unverified. No firmware or automation changes.

## CT relocation, dishwasher and office tests around 16:05–16:06 MDT

User confirms dishwasher CT had been skipped (AFCI/GFCI circuit), moved a CT
there, started dishwasher ~16:05:03, then office laptop/screens ~16:06 and
turned ceiling light off (~20–30 W expected). Exact source CT not supplied.
Input 10 prior ~125 W load disappears; first <5 W sample 16:02:20.
At 16:05:04 it pulses ~53 W, settles ~8 W, then rises to ~51 W around 16:06.
High-confidence dishwasher timing match; asked whether input 10/UPS CT moved.
Map input 10 as dishwasher only from 16:05:04; retain earlier UPS identity.
Do not rewrite raw capture metadata globally or reinterpret historical samples.
Office input 13 rises at 16:06:00 from ~119 W, reaches ~299 W peak, then
settles near ~198 W by 16:07. Promote Office/Entry Study, breaker 10, high
confidence. Light-off step not independently resolved amid computer variability.
No firmware, HA, Node-RED, or raw capture changes.

## Claire mini-split toggle around 16:08 MDT

User reports going to toggle Claire mini split. Input 12 changes from ~3 W
to ~57–65 W; first >20 W sample 16:08:10. Other unresolved inputs
remain near their baselines. Together with prior repeated ramp/taper matching,
assign Claire Mini Split high confidence, proposed power/energy multiplier 2.
Actual breaker pair and phase/polarity remain unverified.
User accepts possible UPS CT relocation to dishwasher; old physical source is
still not directly confirmed. No attempt to restore full panel CT coverage.
Current strong assignments: 1,2,3,4,5,7,10,11,12,13 (ten). Remaining inputs:
6 Washer+Bidet candidate; 8 Water Heater candidate; 9 Utility Closet weak
candidate; 14 unknown; 15 unknown; 16 Upstairs Beds weak candidate.
No firmware, HA or automation changes.

## Kids/master vacuum and bidet tests — 16:10–16:12 MDT

User reports kids-bedroom vacuum ~5 s, then master-bedroom vacuum ~5 s,
then closet light and bidet heater use around 16:11.
First event: Mains A magnitude rises from ~2696 W to peak ~4191 W at
16:10:20–24; no corresponding branch-power spike across all 16. Mains A
then remains ~3070 W until 16:10:45, so not all additional draw is uniquely
attributable to the brief vacuum. Tested kids outlet likely lacks branch CT
coverage. Input 16 stayed ~166 W; prior Upstairs Beds guess weakened.
Second event: input 14 ~36 W baseline rises 16:10:47–53, peak ~1341 W,
then returns to ~36 W. Assign Master Bedroom / Bath, panel breaker 6, high
confidence. Later ~61 W then ~78 W background changes do not isolate the
reported ~7 W closet bulb by itself.
Bidet: input 6 ~58 W baseline to ~134 W around 16:11:10, then variable
heater draw peaking ~654 W around 16:11:40, falling near ~117 W by 16:12.
User confirms washer and bidet share circuit, so assign Washer + Bidet,
breaker 15 per panel label, high confidence. Neither circuit requires doubling.
Strong current assignments now 12 of 16; unresolved 8,9,15,16. No firmware
or HA/automation changes.

## Four outlet vacuum tests — 16:17–16:19 MDT

User reports closet at 16:17, loft around clock changing to 16:18, kids
bathroom still 16:18, laundry GFCI 16:19, each ~5 seconds. Observed:
- Input 14, 16:17:14–18, peak 1273 W: closet, same circuit as prior master
  bedroom outlet. Rename descriptive load Master Bedroom / Closet; printed
  Master Bed/Bath label does not establish coverage of all bathroom outlets.
- Input 16, 16:17:57–16:18:02, peak 1618 W: loft. A few seconds difference
  from user clock estimate is consistent with reported approximate timing.
- Input 16, 16:18:39–43, peak 1426 W: kids bathroom outlet. Assign Loft /
  Kids Bathroom high confidence. Smoke-detector coverage not tested; earlier
  kids bedroom outlet remains apparently unmonitored. Printed breaker unknown.
- Input 6, 16:19:03–07, peak 1481 W: laundry GFCI, same input as tested bidet.
  User previously states washer shares bidet circuit. Expand descriptive label
  to Washer / Bidet / Laundry GFCI. Exact breaker association remains based on
  panel labels rather than direct breaker isolation.
13 of 16 current inputs now strongly assigned; unresolved 8 (Water Heater),
9 (weak Utility Closet), 15 (unknown). No firmware/HA changes.

## Water-heater test — 16:25:08 MDT

User starts hot water ~16:24 and hears relay/heater startup ~16:25. Input 8
rises from ~2 W at 16:25:08, peaks 310.7 W, settles ~179–188 W and remains
running through 16:26:03 inspection. Confirms Water Heater, high confidence,
breaker 23 per printed label, power/energy multiplier 1.
Input 15 also shows a brief ~287–289 W episode around 16:25:10–20 against
~72 W background; returns ~74 W by 16:25:30. Identity/cause remains unresolved.
14 of 16 branches assigned; inputs 9 and 15 remain unresolved. No firmware
or HA/automation changes.

## Family-room and entryway vacuum tests — 16:27 MDT

User reports family-room outlet first, then entryway outlet, ~5 seconds each.
Two clear Mains A magnitude excursions appear around 16:27:20–27 (strong
portion 22–26, ~6.03 kW background to ~7.28 kW peak) and 16:27:47–52
(~6.02 kW background to ~7.50 kW peak). All 16 branch powers checked across
both events; none has a corresponding vacuum-sized pulse. Input 9 stays
~78–79 W; input 15 ~84–100 W. Both tested outlets likely lack individual
branch CT coverage. Same-main response does not establish same breaker.
A separate sustained dryer input-2 heater load is active across both windows
(~2.8 kW single-leg), explaining high mains background; it is not either
vacuum pulse. No new assignment. Inputs 9 and 15 remain unresolved.

## Garage lights report and new input 9 event — around 16:28 / 16:31:52

User suggests garage NVR/outside LEDs as unknown background and reports
toggling main garage LEDs (~300 W expected) around 16:28 or shortly after
previous message. All branch powers checked 16:27:50–16:29:30; no matching
300 W branch step, input 9 stayed 78–79 W. At 16:31:52 input 9 rises to
309 W then ~503–517 W through 16:32:04; by 16:32:06 falls ~115 W, then
~109 W. This resembles a short motor run followed by added light load, but
identity is unverified. Asked whether garage door/other garage load operated
around 16:31:52. Garage candidate recorded only; do not promote assignment
without explaining timing and waveform mismatch. No firmware/HA changes.

User confirms HA big-garage-door opened timestamp 16:31:51. Input 9
activation at 16:31:52 matches within one second. Assign Garage / Big Door
Opener high confidence, multiplier 1. NVR/exterior/main LED coverage and
printed breaker identity remain unverified. This supersedes Utility Closet
candidate. 15 of 16 current branches assigned; only input 15 unresolved.
No firmware or automation changes.

## Whole-house fan and amplifier tests — around 16:40 MDT

User reports whole-house fan low (~80 W expected) for ~30 seconds, then
amp trigger off ~10 seconds, on again and five-zone music continuing.
Input 16 rises from ~166 W at 16:40:30, ramps to ~248 W (+82 W), then
returns ~166 W at 16:41:00. Confirms whole-house fan on Loft/Kids Bathroom
circuit. Does not establish radon fan or smoke-detector coverage.
Input 15 before median ~47.6 W (16:38–39) vs after ~46.0 W (16:41:40–
16:42:14), overall ~38–58 W in test window; no identifiable 10-second
amp-off notch or sustained playback rise. Amplifier remains unlocated; absence
of a clear increase cannot exclude circuit if power difference is small.
Input 13 drops ~166 to ~114 W at 16:40:41 and stays lower; does not match
the reported brief off followed by resumed playback, so do not assign amp.
15 of 16 mapped; input 15 remains unknown. No firmware/HA changes.

## Basement vanity two-stage light test — 17:02 MDT

User reports basement vanity one channel (~20 W), then both channels
(~40 W), around 17:01–02. Input 15 ~24 W baseline rises to ~42 W
17:02:01–08, then ~59–64 W 17:02:09–13, returns near baseline by
17:02:16. Incremental stages ~18 W and ~36 W above background closely
match reported sequence. Assign Basement Bathroom, high confidence,
power/energy multiplier 1. Printed breaker 25 Basement Bath GFCI is only
a candidate due to relocation uncertainty. Basement tank-heater bidet may
explain background/bursts but user only suspects shared circuit, not confirmed.
All 16 current branch inputs now have high-confidence load identities; this
does not establish complete outlet coverage, physical phases/polarities, or
exact breaker numbers. Firmware remains numbered/raw, automations unchanged.
