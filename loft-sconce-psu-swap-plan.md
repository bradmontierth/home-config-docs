# Loft sconce rewire + LED PSU cascade plan

Decided 2026-08-31 (conversation w/ Claude). Status: **planned, nothing bought/built yet.**

## Summary of decisions

1. **Loft sconce**: retire the in-fixture Dig Uno + An Penta Mini. New **An Penta Plus #2 in Simon's closet** (Ethernet), ~10 ft run to the sconce. The fixture becomes dumb (strips + one multi-core cable, no controllers/Wagos inside).
2. **Simon's closet power**: the master-closet **LRS-200-24 moves here** (with its TRC enclosure) and feeds both Simon's crown Penta Plus and the new loft Penta Plus. Retire the 4 A desktop brick.
3. **Master closet power**: replace with **HLG-480H-24** (fanless, potted, IP67 — needs no enclosure) + metal handy-box splice to a plug-in appliance cord. Sized for the future **master crown molding** project.
4. Old 25-ft 16/2 (master closet → loft) is **abandoned in place** as future pathway. Do NOT pull it out.

## Why (engineering rationale)

- The in-fixture-controller layout wasn't buying what it was built for: with low-side
  switching, the 25-ft feed still carries 20 kHz chopped current. The real remote-controller
  risks are (1) digital data integrity — fine at ~3 m through the Penta's level shifter,
  (2) voltage drop — ~0.25 V worst case on 18 AWG at 2 A, (3) PWM EMI — negligible at 10 ft
  with conductors bundled in one jacket (loop area is what radiates).
- Crosstalk from analog PWM negatives onto the digital data line in a shared jacket is a
  non-issue at 10 ft: QuinLED slows gate edges, coupled spikes ≲1 V vs 5 V logic thresholds,
  level shifter drives low-impedance. Data MUST travel with its own ground in the same
  jacket (tight loop = the immunity). Failure mode if wrong = visible pixel sparkle;
  fixes: spare conductor as 2nd data-ground, sacrificial pixel, slower data rate.
- **LRS-350 has a 60 mm fan** (LRS series is fanless only ≤200 W) — that's the noisy garage
  unit's problem; off the table for a bedroom closet. UHP-500/750 are fanless but have
  exposed screw terminals (only 3D-printed covers, no TRC steel enclosure exists) and cool
  by conduction through the baseplate (want an aluminum mounting plate). HLG-480H needs
  neither: 90 °C case rating, free-air convection, screw it to a board.

## Load math

**Loft sconce**: max 2 A @ 24 V (48 W). 3 white analog channels + 1 digital strip.

**Simon's closet (LRS-200-24, 8.8 A / 211 W)**
- Crown: capped 4 A today (13×11 room, 48 ft perimeter; caps exist because mixing
  white+color hits ~12 W/m and the old 4 A brick was the wall)
- Loft: 2 A
- Total 6 A = 68 %. Crown cap can be raised 4 A → ~5 A (80 % total); beyond that too tight.

**Master closet (HLG-480H-24, 20 A / 480 W)**
- Future crown: perimeter 2×(15.5+14) + 8 + 4 bump-outs = 71 ft ≈ 21.6 m.
  Strips: 5 W/m white, ~7 W/m color, ~12 W/m uncapped mix → **uncapped worst ≈ 260 W**.
  No software cap needed at this supply size.
- Master bath vanity: 2 m × 20 W/m = 40 W
- Stair fixture: 5 pendants × 2 ft × 8 W/m ≈ 24 W peak (run at 70 % but PWM ⇒ size to peak)
- Worst case ≈ 325 W = 68 % of 480 W. (LRS-200 would have been 82 % on the *tame* crown
  scenario with zero mix headroom — hence the upgrade.)

## Wiring — Simon's closet → loft sconce

One pull of **18/8 thermostat/security wire (CL2/CL3)**, ~12 ft (~$1.33/ft):

| Conductor | Use |
|---|---|
| 1 | analog +24 V (shared, sized fine: ~0.25 V drop worst case) |
| 2–4 | white ch 1/2/3 switched negatives |
| 5 | digital +24 V |
| 6 | digital data |
| 7 | digital ground |
| 8 | spare — land as 2nd digital ground |

Fallback if pixel sparkle ever appears (not expected): sacrificial pixel, lower data
rate, or move data+gnd to its own small cable (two self-contained circuits in two
jackets is also electrically sound).

## Master closet HLG-480H install (code-compliant 120 V)

Handy box beside the supply; HLG factory input lead in one ½" KO, appliance cord in the
other, both via **strain-relief cord grips** (not NM clamps). Splices inside: brown→black
(L), blue→white (N), grn/yel→green (G) + green pigtail to box ground screw. Blank cover,
plug into existing top-shelf outlet. Check HLG lead OD vs grip range (Arlington
LPCG50 .200–.485" is the safe pick; Halex .260–.375" fits the 16/3 cord).

## Shopping list

- An Penta Plus (#2) — loft, in Simon's closet
- HLG-480H-24 (plain or -A suffix; NOT -B — its dimming input is redundant w/ Penta) ~$110–140
- ~12–15 ft 18/8 CL2/CL3 t-stat/security wire
- [RACO 8660 handy box](https://www.homedepot.com/p/4-in-H-x-2-in-W-x-1-7-8-in-D-Steel-Gray-1-Gang-Drawn-Handy-Box-with-Ten-1-2-in-KO-s-and-Raised-Ground-1-Pack-8660/100560024) + [RACO 860 blank cover](https://www.homedepot.com/p/RACO-1-Gang-Handy-Box-Blank-Cover-860/202056194)
- 2× cord grips: [Arlington LPCG50](https://www.amazon.com/Arlington-LPCG50-10-Electrical-Connector-10-Pack/dp/B00303FYKA) or [Halex 21692](https://www.amazon.com/Halex-21692-2-Inch-Strain-Connector/dp/B000VYK6YK)
- [Southwire 6 ft 16/3 SJT appliance cord](https://www.homedepot.com/p/Southwire-6-ft-16-3-SJTW-13-Amp-125-Volt-Replacement-Power-Supply-Cord-Black-9706SW8808/301132790) (13 A vs HLG's ~5 A draw)
- Master crown: LEDs already bought; **molding not yet purchased**

## Sequencing

1. Buy parts. Master crown molding project is the driver for the HLG but doesn't block
   the loft/Simon's work.
2. Simon's closet: install Penta Plus #2 + Ethernet, pull 18/8 to loft, gut sconce
   controllers, land strips.
3. Move LRS-200 (+ TRC enclosure) master → Simon's; retire 4 A brick. Both crown + loft
   now on it.
4. Master closet: HLG-480H + handy box; vanity + stairs move over; crown lands here later.
5. Leave the 16/2 in the wall.
