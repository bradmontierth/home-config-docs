# Podcast fixture review

The four `truth/*.json` files were manually adjudicated and approved on
2026-08-18. See `MANUAL_REVIEW_2026-08-18.md` for evidence and corrections, and
`ANNOTATION_POLICY.md` for the inclusion and boundary rules.

Run `python3 run.py --validate-only` at any time. A normal run still refuses any
fixture whose status is not `approved`. `--allow-unreviewed-truth` exists only
for diagnostic work and its scores must not be treated as benchmark numbers.

Saved predictions can be re-scored after a truth revision without another model
run:

```bash
python3 rescore.py ../results/podcast-RUN_ID.json
```

The runner directly imports the active `/home/pi/podcast/bin/chapterize_transcript.py`
and calls `run_local_shadow(..., primary=True)`. It writes only beneath the
benchmark result directory and does not invoke the production CLI's database
logging or publishing path.
