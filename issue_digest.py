"""Owner-requested monthly open-issue reminder; no model or automatic fixes."""
from datetime import datetime, timedelta, timezone

import review
import store
import tasking

FIRST_MONTH = "2026-10"


def text(conn, lang, since=None):
    rows = conn.execute("SELECT kind,detail,occurrences FROM issue_patterns"
                        " WHERE status='open' ORDER BY kind,last_seen_at DESC").fetchall()
    if not rows:
        return ""
    grouped = {}
    for row in rows:
        kind = row["kind"]
        group = grouped.setdefault(kind, [0, []])
        group[0] += row["occurrences"]
        example = tasking.redact_derived_text(row["detail"] or "")[:150]
        if len(group[1]) < 2 and example not in group[1]:
            group[1].append(example)
    resolved = conn.execute("SELECT COUNT(*) FROM issue_patterns WHERE status='resolved'"
                            " AND resolved_at>=?", (since or FIRST_MONTH + "-01",)).fetchone()[0]
    lines = ["Мои открытые проблемы за месяц:" if lang == "ru" else "My open issues this month:"]
    for kind, (count, examples) in grouped.items():
        lines.append(f"• {review._issue_label(kind, lang)} ×{count}: " + "; ".join(examples))
    lines.append((f"Закрыто с прошлого отчёта: {resolved}." if lang == "ru"
                  else f"Resolved since the previous digest: {resolved}."))
    lines.append("Ничего не меняю сама. Скажи, что исправить." if lang == "ru"
                 else "Tell me which items you want fixed.")
    return "\n".join(lines)[:3800]


class IssueDigestMixin:
    def check_monthly_issue_digest(self):
        now = datetime.now(timezone.utc)
        local = now + timedelta(hours=self.tz_offset())
        month = local.strftime("%Y-%m")
        if month < FIRST_MONTH or local.day < 7 or local.hour < 10:
            return
        if store.kv_get(self.conn, "issue_digest_month") == month:
            return
        if self._sched_backing_off("issue_digest", now):
            return
        body = text(self.conn, self.lang(), store.kv_get(self.conn, "issue_digest_delivered_at"))
        if not body or self._send_all(body):
            store.kv_set(self.conn, "issue_digest_month", month)
            if body:
                store.kv_set(self.conn, "issue_digest_delivered_at", now.isoformat())
            store.kv_set(self.conn, "issue_digest_fails", "0")
        elif self._sched_send_gave_up("issue_digest"):
            store.kv_set(self.conn, "issue_digest_month", month)
