# ZEN16 whole-house fan — enclosure plan (Claire's closet)

Planned 2026-08-31. **INSTALLED 2026-09-03**: box mounted on wall, ZEN16 on the
3D-printed keyhole adapter plate (works well), wires enclosed and immobile.
One loose end: NM cables are NOT yet clamped — press-in clamps were unworkable
(twin-screw style is the way; had none on hand). Add Halex twin-screw clamps
(shopping list below) on the next HD run so a cable tug can't land on the
ZEN16 terminals.

Originally ranked #1 of the fall projects (safety: the ZEN16 previously sat on
Claire's closet top shelf switching 120 V with NO enclosure).

## Design decisions

- **PVC junction box, not metal, not 2-gang.** Two reasons:
  1. ZEN16 is a Z-Wave radio — a grounded steel box is a Faraday cage (weak mesh
     link / phantom unavailability is a known failure mode for multirelays in
     metal 4-squares).
  2. ZEN16 body is ~3.9" long — even a 4"-square (2-gang) box is wall-to-wall
     with no room for splices. 6×6×4 is the right size.
- Box mounts surface-flush to the wall at the ceiling line, **cover facing OUT**
  (service access to ZEN16 buttons/LED; a down-facing cover fights gravity).
- Exterior mounting lugs would hold the box off the ceiling: if the lugs are on
  two opposite edges, just rotate the square box 90° so they point left/right;
  if one still lands ceiling-side, nip it flush (cosmetic, not structural).
  Primary mounting = **2 pan-head screws through the interior back wall** into
  drywall anchors — box sits dead flush, heads hide behind the ZEN16.
- **ZEN16 mounts on a 3D-printed adapter plate** (decided 2026-08-31): flat
  plate with two mushroom-head studs at the ZEN16 keyhole spacing + countersunk
  through-holes for the box's back-wall screws between them. Wall screws clamp
  plate + box; ZEN16 hangs on the studs, lifts off for service. Measure keyhole
  center-to-center off the ACTUAL device, not a spec sheet. Parametric model:
  `zen16-mount-plate.scad` (all dims are placeholders to verify with calipers).
- **No knockouts on these boxes** — they come blank; drill your own with a step
  bit. 7/8" hole = standard ½" trade-size KO, so normal fittings seat perfectly.
- **Flush-to-ceiling cable entry (decided 2026-08-31):** the Romex enters the
  TOP face, which is the face against the ceiling — so a twin-screw NM clamp
  (body outside, ~1" tall) can't live there. Use **push-in / snap-in NM
  connectors** instead (Halex 27511 "Hit Lock" or Arlington NM94 Black Button):
  body + spring gate sit inside the box, only a ~1/16" flange shows outside, no
  locknut. Brad has some on hand. A clamp IS still required — NEC 314.17(C)
  makes NM entering a nonmetallic box be secured to the box; the no-clamp
  exception is single-gang ≤ 2¼×4" only. Do NOT reverse-mount a twin-screw
  clamp (locknut outside): still ⅛" proud, outside its listing, eats interior
  depth.
  - Snap the connectors in from OUTSIDE **before the box goes up**, then lift
    the box to the ceiling threading the cables through.
  - Either accept the ~1/16" flange standoff (caulk/paint line hides it) or open
    the ceiling drywall hole at each cable to ~1–1⅛" so the flange nests up in
    the ceiling and the box lands dead flush.
  - **Test one first:** push-ins are marketed for steel boxes (latches sized
    for ~1/16" sheet); the Carlon wall is ~⅛". Drill one hole, snap one in,
    tug. If it won't latch on the PVC → fallback below.
  - **Fallback = drop the box ~1¼" below the ceiling** and use the Halex
    twin-screw clamps in the gap (tighten clamps with the box off the wall,
    then mount). A 1" stub of NM following the surface is legal as-is — 334.15
    only requires exposed NM to closely follow the surface and be protected
    where subject to physical damage; a 1" stub above a box in a closet ceiling
    corner isn't. No smurf tube/sleeve. Paint the jacket to match.
- USB-C wall wart stays OUTSIDE at the outlet; only the cable enters the box.

## Parts

| Part | Item | Notes |
|---|---|---|
| Box + cover | [Carlon E987R-3-HD 6×6×4 PVC junction box](https://www.homedepot.com/p/Carlon-6-in-x-6-in-x-4-in-Gray-PVC-Junction-Box-E987R-3-HD/100404096) | ~$13, screw-on cover included |
| Romex entry (primary) | Push-in NM connector, ½" KO — [Halex 27511 Hit Lock](https://www.acehardware.com/departments/lighting-and-electrical/boxes-fittings-and-conduit/cable-connectors/3007156) (listed for 14/3) or Arlington NM94 Black Button | **on hand.** one per 14/3 cable (2 if feed + switch leg arrive separately). Flush-face fitting: flange outside ~1/16", everything else inside. Verify it latches on the ⅛" PVC wall |
| Romex entry (fallback) | [Halex 3/8" NM twin-screw clamps, ½" KO, 5-pk](https://www.homedepot.com/p/Halex-3-8-in-Non-Metallic-NM-Twin-Screw-Cable-Clamp-Connectors-5-Pack-20511/100133208) | only if push-ins won't hold in PVC → box drops ~1¼" below ceiling, clamps live in the gap |
| USB-C entry | ½" snap-in bushing (Arlington 4400 style) | USB cable ~0.15" OD is too skinny for any cord grip; bushing just protects the drilled edge. USB-C plug head passes a 7/8" hole fine |
| Splices | Wagos / wire nuts | from bin |
| ZEN16 mount | VHB tape (or screws) to box back | 4" depth keeps terminals accessible |

No bonding pigtail (plastic box), but splice all Romex grounds through.

## Drilling notes

- 7/8" step bit, low RPM, light pressure; drill before mounting (or back with
  scrap) so breakthrough doesn't crack the wall. Deburr so locknuts seat flat.
- Hole placement: Romex entries on the TOP face (box is flush to ceiling — cable
  drops straight in, no visible loop) with push-in connectors snapped in BEFORE
  mounting (see design decisions); USB entry bottom/side facing the outlet.
  Keep holes clear of the molded cover-screw bosses in the corners — they crowd
  locknuts.

## Wiring pattern (14/3, adjust to how the runs actually land)

- Black (line hot) → relay COM
- Red (to fan) → relay NO
- White neutral spliced straight through — ZEN16 relays are dry contacts, USB
  does all the powering; neutral never touches the device
- All grounds spliced together
- Short service loop on the USB cable inside; excess stays outside the box

## Code note

Line voltage + low-voltage cable in one enclosure is normally disallowed, but
NEC 725.136(B) permits a Class 2 circuit that is **functionally associated**
with the equipment sharing the box — the USB powering the relay that switches
this circuit qualifies. A random LV cable merely passing through would not.
