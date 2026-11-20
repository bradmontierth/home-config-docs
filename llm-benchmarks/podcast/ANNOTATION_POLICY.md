# Podcast ad truth annotation policy

Version: 1.0.0

The benchmark measures whether the production pipeline removes content a
listener would reasonably skip as advertising or monetization. Apply these
rules consistently across shows:

## Include

- paid third-party commercials and host-read sponsor segments;
- dynamically inserted ads and cross-promotions for other podcasts;
- sustained house monetization pitches, including paid subscriptions,
  Patreon/support appeals, and promoted products or services owned by the
  publisher;
- short explicit sponsor teases when they interrupt editorial content.

## Exclude

- normal episode introductions, closings, credits, and editorial transitions;
- incidental product or company mentions in editorial discussion;
- ordinary show navigation such as feedback addresses, social accounts,
  livestream schedules, and non-monetized programming notes;
- a network/show-identification bumper unless it is inseparable from a
  continuous advertising block.

## Boundaries

- Start at the first promotional utterance and end when editorial content
  resumes.
- Use transcript segment boundaries. Do not invent sub-segment precision that
  the frozen fixture cannot reproduce.
- Merge back-to-back promotions into one block, including tiny silence or
  transition gaps between them. Split blocks when editorial content resumes.
- For a preroll, use 0.0 as the start; for a postroll that runs to completion,
  use the transcript duration as the end.

## Canonical evidence

The frozen timestamped transcript is canonical. Podcast RSS audio commonly
uses dynamic ad insertion: fetching the same episode URL later can return
different ads and different timing. During the 2026-08-18 review, the current
Pod Save America URL at the archived opening timestamp returned Cariloha and
Long Play promotions, while the frozen transcript contains SimpliSafe, Minky
Couture, Nabisco, and the original show opening. Current RSS audio therefore
cannot reliably adjudicate the historical timeline.

