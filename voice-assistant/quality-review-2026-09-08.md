# First labeled microphone-quality review — 2026-09-08

Reviewed seven observation turns from 13:03–13:06 America/Denver: five labeled
pairs and two unpaired turns. Evidence: live `/quality/recent`, corresponding
turn rows, downloaded PCM recordings (level measurements), satellite logs and
live kitchen capture configuration. Subjective clarity judgments below are
Brad's saved labels/listening notes, not independent listening claims.

## Labeled pairs

Higher (less negative) confidence is preferred. These are whole-hypothesis
scores, including the wake phrase. Times below are observation timestamps
shown by the review page, not the start of the wake.

| Time | Label / conditions | Kitchen score | Family-room score | Ranking | Transcript result |
|---|---|---:|---:|---|---|
| 13:03:51 | Kitchen; no conditions recorded | -0.0442 | -0.1101 | Kitchen | Both: “What day is it?” |
| 13:04:10 | Either; kitchen, fan on; family room sounds clearer | -0.0679 | -0.0863 | Kitchen | Both: “What time is it?” |
| 13:04:31 | Either; kitchen, fan on; family room sounds clearer | -0.0316 | -0.0447 | Kitchen | Both: “What's the weather outside?” |
| 13:05:14 | Family room | -0.1120 | -0.0188 | Family room | Kitchen wake becomes “Okay, Peter”; family room clean |
| 13:05:46 | Family room | -0.1209 | -0.0971 | Family room | Kitchen “What gains it?”; family room “What day is it?” |

The score ranking matches all three decisive labels. Both remaining labels
allow either mic and both transcripts agree. This is encouraging, but not five
independent demonstrations of corrected ASR and not enough to calibrate a
switching margin. There is one unambiguous command-content correction in this
sample; the other family-room pair fixes the wake phrase and its stripping.

The original winner was kitchen in all five paired tests. Family-room shadow
transcripts completed 391, 608, 456, 651 and 729 ms BEFORE their respective
primary results (median 608 ms earlier). These five pairs would have needed
no extra wait at final-result selection. This is capture/arrival/decode timing,
not proof that the family-room model is inherently faster. Two useful swaps
would have been ready 651 and 729 ms earlier.

The useful “What day is it?” correction has only a 0.0238 score advantage.
A fixed 0.05 margin would miss it. Conversely, the fan-on clips sound clearer
on family room to Brad but confidence favors kitchen. Confidence is evidence
about the decode, not a direct measure of acoustic clarity or physical room.

## Kitchen level drop

Live configuration is still `MIC_CHANNELS=2`, `CMD_CHANNEL=1`,
`CMD_CHANNEL_GAIN=1.0`, `MIC_DEVICE=plughw:CARD=Array`.

The September 3 AEC fix stitches **2.5 s of ch0 wake pre-roll** to **ch1 command
audio**. Ch0 is the processed comms/array output (AEC, beamforming, noise
suppression and AGC); the documented ch1 routing is the linear AEC residual of
mic 3, without that post-processing. This avoids the post-chime suppressor
hangover that was deleting first words. The switch follows the capture boundary,
not recognition of the last word “computer.”

For the 13:04:10 fan-on kitchen test, the loudest 250 ms window measured
-14.5 dBFS in kitchen pre-roll and -31.8 dBFS in its command: a 17.3 dB drop.
The family-room recording of that utterance was -20.8 / -21.1 dBFS, a 0.3 dB
change. These compare different speech segments and cannot isolate channel
gain from speech dynamics, but agree with the reported audible level step.

For the 13:05:46 family-room test, command-window peaks were -40.9 dBFS on
kitchen versus -17.4 dBFS on family room. Here the faint kitchen capture also
produced the wrong words. Fan-on command level-contrast diagnostics were
3.64/4.48 dB on kitchen and 7.97/9.26 dB on family room. These are percentile
energy contrasts, not calibrated speech SNR.

## Unpaired turns

- 13:03:33: kitchen used Parakeet after Cohere timeout; no family-room wake row
  or satellite event in that interval. No paired quality conclusion available.
- 13:06:05: family room answered correctly. Kitchen's wake verification was
  rejected at score 78.3 with “Okay, Peter.” This is not a lost observation:
  kitchen was never a verified/suppressed candidate for that turn.

## Recommendation

Keep observation mode while collecting a broader mix of commands, particularly
numbers, durations, names, and negations from both rooms with fan/music on/off.
Judge the best recording/transcript rather than require the physically nearest
mic to win. Evaluate command-only confidence separately from wake-phrase
confidence before calibrating thresholds; the “Okay, Peter” case shows why.
Retain abstention for missing/invalid evidence and bounded waiting for late
shadows. Do not enable a switching threshold from these five pairs alone.
No device configuration, model settings or winner behavior changed in this review.
