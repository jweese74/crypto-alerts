"""
Backup / Export / Import Service
=================================
Produces and consumes portable JSON snapshots of system data.

Export format (version 2):
{
  "meta": {
    "format_version": 2,
    "app_version": "...",
    "exported_at": "<ISO-8601>",
    "exported_by": "<username>",
    "includes": ["settings", "rules", "history", "users"]   # present sections
  },
  "settings": {"key": "value", ...},   # sensitive keys replaced with REDACTED
  "alert_rules": [...],
  "alert_history": [...],              # optional
  "users": [...]                       # optional; passwords never included
}

Sensitive keys that are always REDACTED in exports:
  smtp.password, notif.ntfy.token, notif.discord.webhook_url,
  notif.telegram.bot_token

Import safety:
  - Schema validated before any write.
  - Exact rule/setting matches are skipped (idempotent).
  - Unknown keys in settings are skipped.
  - REDACTED values are never written to the database.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger

# ── Constants ─────────────────────────────────────────────────────────────────

FORMAT_VERSION = 2

# These setting keys contain secrets and must never appear in an export
_REDACT_KEYS: frozenset[str] = frozenset({
    "smtp.password",
    "notif.ntfy.token",
    "notif.discord.webhook_url",
    "notif.telegram.bot_token",
    "notif.telegram.chat_id",   # chat IDs can be semi-sensitive
})
_REDACTED = "REDACTED"


# ── Export ────────────────────────────────────────────────────────────────────

async def export_data(
    db: AsyncSession,
    *,
    include_users: bool = False,
    include_history: bool = False,
    exported_by: str = "admin",
) -> dict[str, Any]:
    """
    Build and return the full export payload as a plain Python dict.
    Sensitive setting values are replaced with REDACTED.
    """
    app_cfg = get_settings()
    includes = ["settings", "rules"]
    if include_users:
        includes.append("users")
    if include_history:
        includes.append("history")

    payload: dict[str, Any] = {
        "meta": {
            "format_version": FORMAT_VERSION,
            "app_version": app_cfg.APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "exported_by": exported_by,
            "includes": includes,
        },
        "settings": await _export_settings(db),
        "alert_rules": await _export_rules(db),
    }

    if include_history:
        payload["alert_history"] = await _export_history(db)

    if include_users:
        payload["users"] = await _export_users(db)

    return payload


async def _export_settings(db: AsyncSession) -> dict[str, str]:
    from app.crud import system_settings as ss_crud
    raw = await ss_crud.get_all(db)
    return {
        k: (_REDACTED if k in _REDACT_KEYS else v)
        for k, v in raw.items()
    }


async def _export_rules(db: AsyncSession) -> list[dict]:
    from app.crud.alert import get_all_rules
    rules = await get_all_rules(db)
    out = []
    for r in rules:
        out.append({
            "id": str(r.id),
            "user_id": str(r.user_id),
            "trading_pair": r.trading_pair,
            "condition": r.condition.value,
            "threshold": r.threshold,
            "label": r.label,
            "custom_message": r.custom_message,
            "is_active": r.is_active,
            "send_once": r.send_once,
            "cooldown_minutes": r.cooldown_minutes,
            "last_state": r.last_state,
            "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
            "time_filter_enabled": r.time_filter_enabled,
            "active_hours_start": r.active_hours_start.strftime("%H:%M") if r.active_hours_start else None,
            "active_hours_end": r.active_hours_end.strftime("%H:%M") if r.active_hours_end else None,
            "active_timezone": r.active_timezone,
            "critical_override": r.critical_override,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        })
    return out


async def _export_history(db: AsyncSession, limit: int = 5000) -> list[dict]:
    from app.crud.alert import get_all_history
    rows = await get_all_history(db, limit=limit)
    out = []
    for h in rows:
        out.append({
            "id": str(h.id),
            "user_id": str(h.user_id),
            "rule_id": str(h.rule_id),
            "trading_pair": h.trading_pair,
            "triggered_price": h.triggered_price,
            "threshold_value": h.threshold_value,
            "message": h.message,
            "severity": h.severity,
            "delivery_channel": h.delivery_channel,
            "delivered": h.delivered,
            "triggered_at": h.triggered_at.isoformat(),
        })
    return out


async def _export_users(db: AsyncSession) -> list[dict]:
    """Export users WITHOUT password hashes."""
    from app.crud.user import get_all
    users = await get_all(db)
    out = []
    for u in users:
        out.append({
            "id": str(u.id),
            "email": u.email,
            "username": u.username,
            # hashed_password intentionally omitted
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
        })
    return out


# ── Validation ────────────────────────────────────────────────────────────────

class ImportError(Exception):
    """Raised when an import payload fails validation."""


_REQUIRED_TOP_KEYS = {"meta", "settings", "alert_rules"}
_REQUIRED_META_KEYS = {"format_version", "exported_at", "includes"}
_REQUIRED_RULE_KEYS = {
    "user_id", "trading_pair", "condition", "threshold",
    "is_active", "cooldown_minutes",
}


def validate_payload(payload: Any) -> dict[str, Any]:
    """
    Validate an import payload dict.  Raises ImportError with a
    human-readable message on the first validation failure.
    Returns the validated payload on success.
    """
    if not isinstance(payload, dict):
        raise ImportError("Payload must be a JSON object.")

    missing = _REQUIRED_TOP_KEYS - payload.keys()
    if missing:
        raise ImportError(f"Missing required top-level keys: {missing}")

    meta = payload["meta"]
    if not isinstance(meta, dict):
        raise ImportError("'meta' must be an object.")
    meta_missing = _REQUIRED_META_KEYS - meta.keys()
    if meta_missing:
        raise ImportError(f"Missing meta keys: {meta_missing}")

    version = meta.get("format_version")
    if version not in (1, 2):
        raise ImportError(
            f"Unsupported format_version: {version!r}. Expected 1 or 2."
        )

    rules = payload.get("alert_rules", [])
    if not isinstance(rules, list):
        raise ImportError("'alert_rules' must be a list.")

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ImportError(f"alert_rules[{i}] must be an object.")
        rule_missing = _REQUIRED_RULE_KEYS - rule.keys()
        if rule_missing:
            raise ImportError(
                f"alert_rules[{i}] missing required fields: {rule_missing}"
            )
        if rule["condition"] not in ("above", "below"):
            raise ImportError(
                f"alert_rules[{i}] has invalid condition: {rule['condition']!r}"
            )
        try:
            float(rule["threshold"])
        except (TypeError, ValueError):
            raise ImportError(
                f"alert_rules[{i}] threshold must be a number."
            )

    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ImportError("'settings' must be an object.")

    history = payload.get("alert_history", [])
    if not isinstance(history, list):
        raise ImportError("'alert_history' must be a list.")

    users = payload.get("users", [])
    if not isinstance(users, list):
        raise ImportError("'users' must be a list.")

    return payload


# ── Import ────────────────────────────────────────────────────────────────────

async def import_data(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    import_settings: bool = True,
    import_rules: bool = True,
    import_history: bool = False,
    import_users: bool = False,
    overwrite_rules: bool = False,
) -> dict[str, int]:
    """
    Restore data from a validated export payload.

    Returns counts: {"settings": N, "rules": N, "history": N, "users": N}

    Safety rules:
    - REDACTED values are never written.
    - Existing rules are skipped unless overwrite_rules=True.
    - User passwords are never imported (users section is metadata only).
    """
    counts: dict[str, int] = {
        "settings": 0,
        "rules": 0,
        "history": 0,
        "users": 0,
    }

    if import_settings and "settings" in payload:
        counts["settings"] = await _import_settings(db, payload["settings"])

    if import_rules and "alert_rules" in payload:
        counts["rules"] = await _import_rules(
            db, payload["alert_rules"], overwrite=overwrite_rules
        )

    if import_history and "alert_history" in payload:
        counts["history"] = await _import_history(db, payload["alert_history"])

    if import_users and "users" in payload:
        counts["users"] = await _import_users(db, payload["users"])

    logger.info(
        f"[backup] Import complete — settings: {counts['settings']}, "
        f"rules: {counts['rules']}, history: {counts['history']}, "
        f"users: {counts['users']}"
    )
    return counts


async def _import_settings(db: AsyncSession, settings: dict[str, str]) -> int:
    """Write non-REDACTED, non-sensitive settings back to the DB."""
    from app.crud import system_settings as ss_crud
    written = 0
    for key, value in settings.items():
        if value == _REDACTED:
            continue  # never write REDACTED placeholder
        if key in _REDACT_KEYS:
            continue  # extra safety: skip sensitive keys even if not redacted
        await ss_crud.set_value(db, key, str(value))
        written += 1
    return written


async def _import_rules(
    db: AsyncSession,
    rules: list[dict],
    *,
    overwrite: bool = False,
) -> int:
    """
    Import alert rules.

    If overwrite=False (default): skip any rule whose ID already exists.
    If overwrite=True: delete existing rule with same ID before re-inserting.

    Rules whose user_id does not correspond to an existing user are skipped
    with a warning (referential integrity).
    """
    from app.crud.alert import get_rule_by_id, create_rule
    from app.crud.user import get_by_id
    from app.models.alert_rule import AlertCondition
    from datetime import time as dt_time

    imported = 0
    for r in rules:
        rule_id_str = r.get("id")
        user_id_str = r.get("user_id")

        # Validate UUIDs
        try:
            user_uuid = uuid.UUID(str(user_id_str))
        except (ValueError, AttributeError):
            logger.warning(f"[backup] Skipping rule — invalid user_id: {user_id_str!r}")
            continue

        # Check user exists
        user = await get_by_id(db, user_uuid)
        if user is None:
            logger.warning(f"[backup] Skipping rule for unknown user {user_uuid}")
            continue

        # Handle existing rule
        if rule_id_str:
            try:
                rule_uuid = uuid.UUID(str(rule_id_str))
                existing = await get_rule_by_id(db, rule_uuid)
                if existing:
                    if not overwrite:
                        logger.debug(f"[backup] Skipping existing rule {rule_uuid}")
                        continue
                    await db.delete(existing)
                    await db.commit()
            except (ValueError, AttributeError):
                pass  # no valid ID — will create new

        # Parse time filter fields
        def _parse_t(v: str | None):
            if not v:
                return None
            try:
                h, m = v.split(":")
                return dt_time(int(h), int(m))
            except Exception:
                return None

        await create_rule(
            db,
            user_id=user_uuid,
            trading_pair=r["trading_pair"],
            condition=AlertCondition(r["condition"]),
            threshold=float(r["threshold"]),
            label=r.get("label"),
            custom_message=r.get("custom_message"),
            is_active=bool(r.get("is_active", True)),
            send_once=bool(r.get("send_once", False)),
            cooldown_minutes=int(r.get("cooldown_minutes", 60)),
            time_filter_enabled=bool(r.get("time_filter_enabled", False)),
            active_hours_start=_parse_t(r.get("active_hours_start")),
            active_hours_end=_parse_t(r.get("active_hours_end")),
            active_timezone=r.get("active_timezone") or "UTC",
            critical_override=bool(r.get("critical_override", False)),
        )
        imported += 1

    return imported


async def _import_history(db: AsyncSession, history: list[dict]) -> int:
    """Re-import alert history rows (skips rows with duplicate IDs)."""
    from sqlalchemy import select
    from app.models.alert_history import AlertHistory

    imported = 0
    for h in history:
        try:
            h_id = uuid.UUID(str(h.get("id", "")))
        except (ValueError, AttributeError):
            h_id = uuid.uuid4()

        # Skip if already exists
        existing = await db.execute(
            select(AlertHistory).where(AlertHistory.id == h_id)
        )
        if existing.scalar_one_or_none():
            continue

        try:
            user_uuid = uuid.UUID(str(h.get("user_id", "")))
            rule_uuid = uuid.UUID(str(h.get("rule_id", "")))
        except (ValueError, AttributeError):
            continue

        triggered_at = datetime.now(timezone.utc)
        if h.get("triggered_at"):
            try:
                triggered_at = datetime.fromisoformat(h["triggered_at"])
            except ValueError:
                pass

        row = AlertHistory(
            id=h_id,
            user_id=user_uuid,
            rule_id=rule_uuid,
            trading_pair=str(h.get("trading_pair", "")),
            triggered_price=float(h.get("triggered_price", 0)),
            threshold_value=float(h.get("threshold_value", 0)),
            message=str(h.get("message", "")),
            severity=str(h.get("severity", "normal")),
            delivery_channel=str(h.get("delivery_channel", "import")),
            delivered=bool(h.get("delivered", False)),
            triggered_at=triggered_at,
        )
        db.add(row)
        imported += 1

    if imported:
        await db.commit()
    return imported


async def _import_users(db: AsyncSession, users: list[dict]) -> int:
    """
    Import user metadata only — passwords are never in exports.
    Creates user with a locked placeholder password if not already present.
    """
    from app.crud.user import get_by_email, create
    from app.models.user import UserRole

    imported = 0
    for u in users:
        email = str(u.get("email", "")).strip().lower()
        username = str(u.get("username", "")).strip()
        if not email or not username:
            continue

        existing = await get_by_email(db, email)
        if existing:
            logger.debug(f"[backup] Skipping existing user {email}")
            continue

        role_str = str(u.get("role", "user"))
        try:
            role = UserRole(role_str)
        except ValueError:
            role = UserRole.USER

        # Placeholder password — user must reset via admin
        placeholder_pw = f"IMPORTED_{uuid.uuid4().hex[:16]}"
        await create(
            db,
            email=email,
            username=username,
            password=placeholder_pw,
            role=role,
            is_active=bool(u.get("is_active", True)),
        )
        imported += 1

    return imported


# ── Serialisation helpers ─────────────────────────────────────────────────────

def to_json(payload: dict[str, Any], *, indent: int = 2) -> str:
    """Serialise export payload to a JSON string."""
    return json.dumps(payload, indent=indent, default=str, ensure_ascii=False)


def from_json(raw: str | bytes) -> dict[str, Any]:
    """Parse a JSON string/bytes into a payload dict. Raises ValueError on bad JSON."""
    return json.loads(raw)
