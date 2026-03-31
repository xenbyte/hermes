import logging

from psycopg2.extras import RealDictCursor

from hermes_utils.db import get_connection, fetch_one, _write

logger = logging.getLogger(__name__)

HAIKU_INPUT_COST_PER_M = 0.80
HAIKU_OUTPUT_COST_PER_M = 4.00
SONNET_INPUT_COST_PER_M = 3.00
SONNET_OUTPUT_COST_PER_M = 15.00

MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (HAIKU_INPUT_COST_PER_M, HAIKU_OUTPUT_COST_PER_M),
    "claude-sonnet-4-20250514": (SONNET_INPUT_COST_PER_M, SONNET_OUTPUT_COST_PER_M),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in dollars."""
    input_rate, output_rate = MODEL_COSTS.get(model, (0.0, 0.0))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def log_usage(batch_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """INSERT into hermes.llm_usage with estimated_cost."""
    cost = _estimate_cost(model, input_tokens, output_tokens)
    _write(
        "INSERT INTO hermes.llm_usage (batch_id, model, input_tokens, output_tokens, estimated_cost) "
        "VALUES (%s, %s, %s, %s, %s)",
        [batch_id, model, input_tokens, output_tokens, cost],
    )
    logger.warning(
        "LLM usage logged: batch=%s model=%s in=%d out=%d cost=$%.6f",
        batch_id, model, input_tokens, output_tokens, cost,
    )


def get_daily_spend() -> float:
    """SUM(estimated_cost) for today (since midnight UTC)."""
    result = fetch_one(
        "SELECT COALESCE(SUM(estimated_cost), 0) AS total "
        "FROM hermes.llm_usage "
        "WHERE called_at >= CURRENT_DATE"
    )
    return float(result.get("total", 0))


def check_daily_budget(limit: float = 2.0) -> bool:
    """Return True if under budget, False if over."""
    spent = get_daily_spend()
    if spent >= limit:
        logger.warning("Daily LLM budget exhausted: $%.4f spent of $%.2f limit", spent, limit)
        return False
    return True


def get_monthly_summary() -> dict:
    """Return summary for the current calendar month.

    Returns:
        {'total_cost': float, 'total_calls': int, 'by_model': {model: {'cost': float, 'calls': int}}}
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT "
                "  model, "
                "  COUNT(*) AS calls, "
                "  COALESCE(SUM(estimated_cost), 0) AS cost "
                "FROM hermes.llm_usage "
                "WHERE called_at >= date_trunc('month', CURRENT_DATE) "
                "GROUP BY model"
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error("Failed to fetch monthly summary: %r", e)
        rows = []
    finally:
        if conn:
            conn.close()

    total_cost = 0.0
    total_calls = 0
    by_model: dict[str, dict] = {}

    for row in rows:
        cost = float(row["cost"])
        calls = int(row["calls"])
        total_cost += cost
        total_calls += calls
        by_model[row["model"]] = {"cost": cost, "calls": calls}

    return {
        "total_cost": total_cost,
        "total_calls": total_calls,
        "by_model": by_model,
    }
