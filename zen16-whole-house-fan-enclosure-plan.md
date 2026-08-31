# ZEN16 whole-house fan — enclosure plan (Claire's closet)

Planned 2026-08-31. Status: **parts not bought.** Ranked #1 of the fall projects
(safety: the ZEN16 currently sits on Claire's closet top shelf switching 120 V
with NO enclosure).

## Design decisions

- **PVC junction box, not metal, not 2-gang.** Two reasons:
  1. ZEN16 is a Z-Wave radio — a grounded steel box is a Faraday cage (weak mesh
     link / phantom unavailability is a known failure mode for multirelays in
     metal 4-squares).
  2. ZEN16 body is ~3.9" long — even a 4"-square (2-gang) box is wall-to-wall
     with no room for splices. 6×6×4 is the right size.
- Box mounts surface-flush to the wall at the ceiling line.
- **No knockouts on these boxes** — they come blank; drill your own with a step
  bit. 7/8" hole = standard ½" trade-size KO, so normal fittings seat perfectly.
- USB-C wall wart stays OUTSIDE at the outlet; only the cable enters the box.

## Parts

| Part | Item | Notes |
|---|---|---|
| Box + cover | [Carlon E987R-3-HD 6×6×4 PVC junction box](https://www.homedepot.com/p/Carlon-6-in-x-6-in-x-4-in-Gray-PVC-Junction-Box-E987R-3-HD/100404096) | ~$13, screw-on cover included |
| Romex entry | [Halex 3/8" NM twin-screw clamps, ½" KO, 5-pk](https://www.homedepot.com/p/Halex-3-8-in-Non-Metallic-NM-Twin-Screw-Cable-Clamp-Connectors-5-Pack-20511/100133208) | one per 14/3 cable entering (2 if feed + switch leg arrive separately) |
| USB-C entry | ½" snap-in bushing (Arlington 4400 style) | USB cable ~0.15" OD is too skinny for any cord grip; bushing just protects the drilled edge. USB-C plug head passes a 7/8" hole fine |
| Splices | Wagos / wire nuts | from bin |
| ZEN16 mount | VHB tape (or screws) to box back | 4" depth keeps terminals accessible |

No bonding pigtail (plastic box), but splice all Romex grounds through.

## Drilling notes

- 7/8" step bit, low RPM, light pressure; drill before mounting (or back with
  scrap) so breakthrough doesn't crack the wall. Deburr so locknuts seat flat.
- Hole placement: Romex entries on the TOP face (box is flush to ceiling — cable
  drops straight in, no visible loop); USB entry bottom/side facing the outlet.
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
