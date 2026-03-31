import logging

from psycopg2.extras import RealDictCursor

from hermes_utils.db import get_connection, fetch_one, fetch_all, _write

logger = logging.getLogger(__name__)


def get_profiles_with_enrichment() -> list[dict]:
    """All profiles that have max_rent and target_cities set."""
    return fetch_all(
        "SELECT * FROM hermes.user_profiles "
        "WHERE max_rent IS NOT NULL AND target_cities IS NOT NULL"
    )


def get_profile_by_id(profile_id: int) -> dict | None:
    """Single profile by PK."""
    result = fetch_one(
        "SELECT * FROM hermes.user_profiles WHERE id = %s", [profile_id]
    )
    return result or None


def get_profile_for_telegram_id(telegram_id: str) -> dict | None:
    """Profile by telegram_id, or None."""
    result = fetch_one(
        "SELECT * FROM hermes.user_profiles WHERE telegram_id = %s",
        [telegram_id],
    )
    return result or None


def upsert_profile(telegram_id: str, fields: dict) -> int:
    """Insert or update profile. Return profile id.

    Uses INSERT ... ON CONFLICT (telegram_id) DO UPDATE.
    Always sets updated_at = now() on update.
    """
    all_columns = [
        "full_name", "age", "nationality", "languages", "bsn_held",
        "gemeente", "employer", "contract_type", "gross_monthly_income",
        "employment_duration", "work_address", "max_rent", "target_cities",
        "furnishing_pref", "occupants", "pets", "owned_items",
        "move_in_date", "extra_notes",
    ]

    provided = {k: v for k, v in fields.items() if k in all_columns}
    if not provided:
        raise ValueError("No valid profile fields provided")

    columns = ["telegram_id"] + list(provided.keys())
    placeholders = ["%s"] * len(columns)
    values = [telegram_id] + list(provided.values())

    update_parts = [f"{col} = EXCLUDED.{col}" for col in provided.keys()]
    update_parts.append("updated_at = now()")

    query = (
        f"INSERT INTO hermes.user_profiles ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (telegram_id) DO UPDATE SET {', '.join(update_parts)} "
        f"RETURNING id"
    )

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, values)
            row = cur.fetchone()
            conn.commit()
            return row["id"]
    except Exception as e:
        logger.error("Failed to upsert profile for telegram_id=%s: %r", telegram_id, e)
        raise
    finally:
        if conn:
            conn.close()


_PROFILE_FIELDS = [
    ("full_name", "Name"),
    ("age", "Age"),
    ("nationality", "Nationality"),
    ("languages", "Languages"),
    ("bsn_held", "BSN held"),
    ("gemeente", "Gemeente registration"),
    ("employer", "Employer"),
    ("contract_type", "Contract type"),
    ("gross_monthly_income", "Gross monthly income (EUR)"),
    ("employment_duration", "Employment duration"),
    ("work_address", "Work address"),
    ("max_rent", "Maximum rent (EUR/month)"),
    ("target_cities", "Target cities"),
    ("furnishing_pref", "Furnishing preference"),
    ("occupants", "Occupants"),
    ("pets", "Pets"),
    ("owned_items", "Owned items"),
    ("move_in_date", "Move-in date"),
    ("extra_notes", "Additional notes"),
]


def build_system_prompt(profile: dict) -> str:
    """Render profile into a structured text block for Claude's system prompt."""
    lines = [
        "You are analyzing Dutch rental listings for the following applicant:",
        "",
    ]

    for key, label in _PROFILE_FIELDS:
        value = profile.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "Yes" if value else "No"
        lines.append(f"{label}: {value}")

    lines.append("")
    lines.append(
        "Use this profile to score compatibility and write motivation letters."
    )

    return "\n".join(lines)
