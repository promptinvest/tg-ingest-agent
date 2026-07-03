#!/usr/bin/env bash
# Cara/Nikki memory split (Phase 4/5 of the 2026-07-03 split plan).
# Runs ON the PD box as root, with BOTH services stopped. One shot, verified.
#
#   bash split-cara-nikki.sh            # perform the split + verify
#   bash split-cara-nikki.sh --verify   # verification only (after a split)
#
# Inputs on the box (staged by deploy or scp):
#   /root/cara-nikki-split/split-curation-2026-07-03.json  (operator-confirmed)
#   /root/cara-nikki-split/baseline-counts.json            (Phase-0 baseline)
# DBs:
#   CARA_DB  /var/lib/tg-ingest-agent/ingest.db  (stripped in place; snapshot exists)
#   NIKKI_DB /var/lib/nikki-agent/nikki.db       (created here from a copy)
set -euo pipefail

CARA_DB="${CARA_DB:-/var/lib/tg-ingest-agent/ingest.db}"
NIKKI_DB="${NIKKI_DB:-/var/lib/nikki-agent/nikki.db}"
WORK="${WORK:-/root/cara-nikki-split}"
MODE="${1:-split}"

if [ "$MODE" != "--verify" ]; then
  for svc in tg-ingest-agent nikki-agent; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      echo "REFUSING: $svc is running — stop it first (systemctl stop $svc)" >&2
      exit 1
    fi
  done
fi

python3 - "$MODE" "$CARA_DB" "$NIKKI_DB" "$WORK" <<'PY'
import json, shutil, sqlite3, sys
from pathlib import Path

mode, cara_db, nikki_db, work = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
curation = json.loads((work / "split-curation-2026-07-03.json").read_text(encoding="utf-8"))
baseline = json.loads((work / "baseline-counts.json").read_text(encoding="utf-8"))

# Tables leaving Cara entirely (companion memory -> Nikki)
COMPANION = ["meetings", "meeting_turns", "meeting_chunks", "meeting_scene", "meeting_prep",
             "relationship_arc", "world_facts", "agreements", "body_state", "intimacy_style",
             "cara_wardrobe", "cara_photos", "stickers"]
# Tables leaving Nikki entirely (notes machinery stays Cara-only)
BUSINESS = ["messages", "categories", "chunks", "urls", "images", "files", "facts",
            "feedback", "list_views"]
# Cara additionally starts CLEAN on these (history moves to Nikki / reseeds on boot)
CARA_TRUNCATE = ["conversation", "relationship_events", "cara_life", "self_facts",
                 "memory_candidates"]
# Nikki starts clean on reminders (they'd double-fire if both bots kept them)
NIKKI_TRUNCATE = ["reminders"]

def tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith("sqlite_")}

def drop(conn, names):
    for t in names:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()

def counts(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    for t in sorted(tables(conn)):
        out[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    conn.close()
    return out

if mode != "--verify":
    assert Path(cara_db).exists(), cara_db
    Path(nikki_db).parent.mkdir(parents=True, exist_ok=True)
    assert not Path(nikki_db).exists(), f"{nikki_db} already exists — refusing to overwrite"
    # 1. full copy via the backup API (safe, DB is closed but be defensive)
    src = sqlite3.connect(cara_db)
    dst = sqlite3.connect(nikki_db)
    src.backup(dst)
    src.close()
    # 2. Nikki DB: business tables out, curated boss rows, reminders clean
    keep_n = set(curation["nikki"]) | set(curation["both"])
    dst.execute("PRAGMA foreign_keys=OFF")
    drop(dst, BUSINESS)
    dst.execute(
        "DELETE FROM boss_profile_items WHERE id NOT IN (%s)"
        % ",".join(str(i) for i in sorted(keep_n)))
    for t in NIKKI_TRUNCATE:
        dst.execute(f"DELETE FROM {t}")
    # spare Nikki the business kv leftovers that would confuse her flows
    dst.execute("DELETE FROM kv WHERE key IN ('note_seq', 'reminders_listed_at',"
                " 'overdue_nudge_at', 'last_sticker_set')")
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    # 3. Cara DB: companion tables out, nikki-only boss rows out, clean start tables
    conn = sqlite3.connect(cara_db)
    conn.execute("PRAGMA foreign_keys=OFF")
    drop(conn, COMPANION)
    nikki_only = set(curation["nikki"])
    conn.execute(
        "DELETE FROM boss_profile_items WHERE id IN (%s)"
        % ",".join(str(i) for i in sorted(nikki_only)))
    for t in CARA_TRUNCATE:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM kv WHERE key IN ('closeness_stage', 'closeness_ceiling',"
                 " 'agreements_surfaced_at', 'agreements_surfaced_ids', 'last_intimate_at',"
                 " 'last_sticker_set', 'greeted_day', 'morning_brief_day')")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print("split done")

# ---- verification ----------------------------------------------------------
cara_c, nikki_c = counts(cara_db), counts(nikki_db)
errors = []

# exclusivity
for t in COMPANION:
    if cara_c.get(t) is not None:
        errors.append(f"companion table {t} still present in Cara DB")
for t in BUSINESS:
    if nikki_c.get(t) is not None:
        errors.append(f"business table {t} still present in Nikki DB")

# moved-table parity vs baseline (Nikki holds the full history)
for t in COMPANION:
    base = baseline.get(t, {}).get("count")
    # nikki may have renamed cara_* tables on first boot; check both names
    n = nikki_c.get(t, nikki_c.get(t.replace("cara_", "nikki_")))
    if base is not None and n != base:
        errors.append(f"{t}: baseline {base} != nikki {n}")
for t in BUSINESS:
    base = baseline.get(t, {}).get("count")
    if base is not None and cara_c.get(t) != base:
        errors.append(f"{t}: baseline {base} != cara {cara_c.get(t)}")

# curation split
keep_n = set(curation["nikki"]) | set(curation["both"])
keep_c_excl = set(curation["nikki"])
conn = sqlite3.connect(f"file:{cara_db}?mode=ro", uri=True)
cara_ids = {r[0] for r in conn.execute("SELECT id FROM boss_profile_items")}
conn.close()
conn = sqlite3.connect(f"file:{nikki_db}?mode=ro", uri=True)
nikki_ids = {r[0] for r in conn.execute("SELECT id FROM boss_profile_items")}
for db, name in ((cara_db, "cara"), (nikki_db, "nikki")):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ok, = c.execute("PRAGMA integrity_check").fetchone()
    if ok != "ok":
        errors.append(f"{name}: integrity_check {ok}")
    fk = c.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        errors.append(f"{name}: {len(fk)} foreign-key violations")
    c.close()
if cara_ids & set(curation["nikki"]):
    errors.append(f"cara still holds nikki-only boss rows: {sorted(cara_ids & keep_c_excl)[:8]}")
if nikki_ids - keep_n:
    errors.append(f"nikki holds unexpected boss rows: {sorted(nikki_ids - keep_n)[:8]}")
base_boss = baseline.get("boss_profile_items", {}).get("count")
if base_boss is not None and len(cara_ids | nikki_ids) != base_boss:
    errors.append(f"boss_profile_items union {len(cara_ids | nikki_ids)} != baseline {base_boss}")

if errors:
    print("VERIFICATION FAILED:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(f"VERIFIED: cara {sum(cara_c.values())} rows / {len(cara_c)} tables;"
      f" nikki {sum(nikki_c.values())} rows / {len(nikki_c)} tables")
PY
