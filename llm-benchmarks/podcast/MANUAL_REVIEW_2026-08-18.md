# Manual truth review — 2026-08-18

Reviewer: Codex (GPT-5), acting as the requested human adjudicator

Method: full-transcript promotional-language scan, inspection of every seeded
and Qwen-predicted region with surrounding editorial context, explicit search
for missed sponsor/subscription calls, and boundary comparison against the
timestamped source segments. The annotation policy is in
`ANNOTATION_POLICY.md`.

All four fixtures are approved. Material corrections follow.

## LRCP — Tour de France Stage 4

- Opening ends at the show return at 115.20, not 122.40.
- The short Joined Cycling/Tour to Join sponsor tease is 246.08–253.84.
- Tour to Join read is 600.24–663.44.
- T-Mobile plus Fantasy Fan Fellas is one continuous block, 1449.68–1552.00.
- Final T-Mobile plus Zyn postroll starts after the hosts finish, at 2120.72,
  not 2118.72.

Approved blocks: 5.

## LRCP — Tour de France Stage 5

- Opening Lead Human, ACAST, and Guardian Football Weekly block is 0–86.00.
- NordVPN is 633.76–747.04.
- Naked Beauty starts at the ACAST insertion at 1544.08; the old 1526.48 start
  incorrectly included 17.60 seconds of race discussion.
- Wealthfront, ACAST, and Nutrition Diva postroll is 2284.56–2381.74.

Approved blocks: 4.

## DTNS — Five Eyes

- Preroll is 0–166.80.
- Patron acknowledgements are publisher monetization under the policy and join
  the immediately following commercial pod: 613.20–876.56.
- Pura and Venmo at 1890.24–1957.28 are unambiguous commercials omitted from
  the old seed.
- The final Patreon call is a distinct house-monetization block at
  2326.32–2333.44.
- The 2344.64 network bumper is excluded; final Smith's and Venmo commercials
  begin at 2350.64 and run to 2424.007.

Approved blocks: 5.

## Pod Save America — JD Is Lame

- Preroll remains 0–113.68. The following frozen segment contains the tail of
  a tagline combined with the show welcome; segment-aligned truth retains that
  mixed segment rather than removing the show opening.
- Crooked's Friends of the Pod paid-subscription pitch at 227.36–254.08 is a
  house monetization block and was missing from the old seed.
- Article plus AG1 starts at 1339.44, not 1328.00.
- ZipRecruiter, Minky Couture, and MessageBox Pro starts at 2860.24, not
  2848.56.
- Quince, Minky Couture, and MessageBox Pro is one continuous block at
  4339.44–4498.96.
- Final Minky Couture, MessageBox Pro, and Culligan block is
  5936.56–6045.288.

Approved blocks: 6.

## Re-scored Qwen 3.6 baseline

The original model outputs were re-scored without another inference run:

- ad-time precision: 97.4%;
- ad-time recall: 94.7%;
- F1: 96.0%;
- approved blocks detected: 19/20;
- fully false predicted blocks: 0.

The one fully missed block is DTNS's short final Patreon appeal. The largest
partial misses are the opening Lead Human promo on LRCP Stage 5 and the patron
acknowledgement portion of DTNS's 613.20–876.56 monetization block.
