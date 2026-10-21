"""Import measure.py loudness into Music Assistant's loudness_measurements (MA must be stopped).

MA's opensubsonic provider ignores ReplayGain tags; MA only uses this table. Rows go in
under both providers that map our songs: opensubsonic (Navidrome song id) and
filesystem_local (library-relative path). Loudness is floored at -29 LUFS so MA (target
-17) never boosts more than +12 dB. Silent files are skipped.

    python ma_import.py <library.db> <library-results.jsonl>
"""
import json, math, sqlite3, sys

FLOOR = -29.0
db, results = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(results)]
ok = [x for x in rows if x.get("lufs") is not None and x["lufs"] > -60 and x.get("peak") is not None]
key = lambda x: x.get("album_id") or x["path"].rsplit("/", 1)[0]
albums = {}
for x in ok:
    albums.setdefault(key(x), []).append(x)
alb = {k: 10 * math.log10(sum(max(y.get("duration") or 0, 1) * 10 ** (y["lufs"] / 10) for y in v)
                          / sum(max(y.get("duration") or 0, 1) for y in v)) for k, v in albums.items()}
c = sqlite3.connect(db)
n = 0
for x in ok:
    L, A = round(max(x["lufs"], FLOOR), 2), round(max(alb[key(x)], FLOOR), 2)
    for item_id, prov in ((x["id"], "opensubsonic--bHkR6yvQ"), (x["path"], "filesystem_local--4Z5yRhf7")):
        c.execute("insert or replace into loudness_measurements(media_type,item_id,provider,loudness,loudness_album)"
                  " values('track',?,?,?,?)", (item_id, prov, L, A))
        n += 1
c.commit()
print("rows written:", n, "| table:", c.execute("select provider, count(*) from loudness_measurements group by provider").fetchall())
c.close()
