"""Durable, conservative reservations for gateway calls, using existing usage storage."""
import store


def reserve(cfg, conn, skill, kind, model, cost, budget_limits, error_type):
    daily, monthly = budget_limits(cfg, conn)
    for period, limit in (("day", daily), ("month", monthly)):
        spent = store.usage_total(conn, period)
        if limit > 0 and spent + cost > limit + 1e-12:
            raise error_type(period, spent, limit)
    return store.usage_add(conn, skill, "unknown_" + kind, model, cost_usd=cost)


def settle(conn, row_id, kind, tokens_in=0, tokens_out=0, seconds=None, cost_usd=0):
    # Only the row created for this request is updated. Historical usage stays intact.
    conn.execute("UPDATE llm_usage SET kind=?, tokens_in=?, tokens_out=?, seconds=?, cost_usd=?"
                 " WHERE id=?", (kind, tokens_in, tokens_out, seconds, cost_usd, row_id))
    conn.commit()


def rejected(conn, row_id, kind, status):
    # Definite request rejection can release a reservation. 5xx, transport errors,
    # timeouts and malformed bodies retain it as unknown usage across restarts.
    if 400 <= status < 500 and status not in (408, 425, 429):
        settle(conn, row_id, "rejected_" + kind)
