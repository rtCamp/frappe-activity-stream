# Copyright (c) 2026, rtCamp and contributors
# For license information, please see license.txt
"""Public engine API for the Activity feed.

Producer apps should NOT import this module directly. They should ship a thin
local shim that lazily + defensively imports `log_activity` so that a missing
`frappe_activity_stream` install never breaks the primary action. See the
implementation plan, section 6.1.
"""

import json

import frappe
from frappe.query_builder.functions import Count

from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream_settings.activity_stream_settings import (
    get_settings_cached,
)

ACTION_GROUPS = {
    "Media",
    "Content",
    "Comment",
    "Membership",
    "Organization",
    "Billing",
    "Account",
    "Auth",
    "Other",
}


def _resolve_source() -> str:
    """Best-effort classification of where the event originated."""
    request = getattr(frappe.local, "request", None)
    if request is not None:
        path = getattr(request, "path", "") or ""
        if path.startswith("/app/") or path.startswith("/desk/"):
            return "Desk"
        if path.startswith("/api/"):
            return "API"
        return "API"
    if getattr(frappe.local, "job", None):
        return "Background Job"
    return "System"


def _resolve_organization() -> str | None:
    """Ask the (optionally) registered producer hook for the current org.

    The engine has no knowledge of what an "organization" is; a business app
    registers `activity_org_resolver` in its hooks.py to supply it. Kept loose
    (via get_hooks) so the engine never hard-depends on any producer app.
    """
    methods = frappe.get_hooks("activity_org_resolver") or []
    for method in methods:
        try:
            org = frappe.get_attr(method)()
            if org:
                return org
        except Exception:
            frappe.log_error(frappe.get_traceback(), "activity_org_resolver failed")
    return None


def _mask_sensitive(meta: dict | None) -> dict | None:
    """Mask sensitive keys anywhere in the meta payload (nested dicts/lists included)."""
    if not meta or not isinstance(meta, dict):
        return meta
    try:
        sensitive = get_settings_cached().get_sensitive_keys()
    except Exception:
        sensitive = set()
    if not sensitive:
        return meta
    return _mask_value(meta, sensitive)


def _mask_value(value, sensitive: set):
    if isinstance(value, dict):
        return {k: ("*****" if k in sensitive else _mask_value(v, sensitive)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(item, sensitive) for item in value]
    return value


def log_activity(
    event_type: str,
    *,
    target_doctype: str | None = None,
    target_name: str | None = None,
    target_label: str | None = None,
    summary: str | None = None,
    organization: str | None = None,
    actor: str | None = None,
    actor_name: str | None = None,
    actor_image: str | None = None,
    meta: dict | None = None,
    action_group: str | None = None,
    source: str | None = None,
    enqueue: bool = False,
) -> None:
    """Record one semantic activity event. Never raises."""
    # Re-entrancy guard: an Activity insert must never trigger more logging.
    if getattr(frappe.local, "_skip_activity_logging", False):
        return
    try:
        settings = get_settings_cached()
        if not getattr(settings, "enabled", 0):
            return

        actor = actor or frappe.session.user
        organization = organization or _resolve_organization()

        if enqueue:
            frappe.enqueue(
                "frappe_activity_stream.api._log_activity_bg",
                queue="short",
                event_type=event_type,
                target_doctype=target_doctype,
                target_name=target_name,
                target_label=target_label,
                summary=summary,
                organization=organization,
                actor=actor,
                actor_name=actor_name,
                actor_image=actor_image,
                meta=meta,
                action_group=action_group,
                source=source or _resolve_source(),
            )
            return

        _insert_activity(
            event_type=event_type,
            target_doctype=target_doctype,
            target_name=target_name,
            target_label=target_label,
            summary=summary,
            organization=organization,
            actor=actor,
            actor_name=actor_name,
            actor_image=actor_image,
            meta=meta,
            action_group=action_group,
            source=source or _resolve_source(),
        )
    except Exception:
        # Logging must NEVER break the primary action.
        frappe.log_error(frappe.get_traceback(), f"log_activity failed: {event_type}")


def _log_activity_bg(**kwargs) -> None:
    try:
        _insert_activity(**kwargs)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"log_activity (bg) failed: {kwargs.get('event_type')}"
        )


def _insert_activity(
    *,
    event_type,
    target_doctype,
    target_name,
    target_label,
    summary,
    organization,
    actor,
    actor_name,
    actor_image,
    meta,
    action_group,
    source,
) -> None:
    if not actor_name or actor_image is None:
        resolved_name, resolved_image = _resolve_actor(actor)
        actor_name = actor_name or resolved_name
        if actor_image is None:
            actor_image = resolved_image

    if action_group not in ACTION_GROUPS:
        action_group = "Other"

    meta = _mask_sensitive(meta)

    frappe.local._skip_activity_logging = True
    try:
        doc = frappe.get_doc(
            {
                "doctype": "Activity",
                "organization": organization,
                "actor": actor,
                "actor_name": actor_name,
                "actor_image": actor_image,
                "event_type": event_type,
                "action_group": action_group,
                "summary": summary or event_type,
                "target_doctype": target_doctype,
                "target_name": target_name,
                "target_label": target_label,
                # NOTE: field is `event_meta`, NOT `meta` — `meta` is a reserved
                # attribute on Frappe Documents (doc.meta = the DocType meta), so a
                # field named `meta` never receives its value and the JSON column's
                # CHECK(json_valid) fails on the resulting empty string.
                # Always store valid JSON ("{}" minimum) to satisfy the constraint.
                "event_meta": json.dumps(meta or {}, default=str),
                "source": source,
                "datetime": frappe.utils.now_datetime(),
            }
        )
        doc.insert(ignore_permissions=True)
    finally:
        frappe.local._skip_activity_logging = False


def _resolve_actor(actor: str | None) -> tuple[str | None, str | None]:
    if not actor or actor == "Guest":
        return (actor, None)
    row = frappe.db.get_value("User", actor, ["full_name", "user_image"], as_dict=True)
    if not row:
        return (actor, None)
    return (row.full_name or actor, row.user_image)


def _count_with_or_filters(filters: list, or_filters: list) -> int:
    """DB-side COUNT for a filters + OR-filters query.

    `frappe.db.count` does not support or_filters, and passing an aggregate through
    `get_all(fields=...)` is not portable: the raw "count(*) as total" string is
    rejected on v16, while the v16 dict spec ({"COUNT": "*"}) is rejected on v15.
    Query Builder works on both, and keeps this a single scalar row instead of
    materializing every matching name (which would be O(n) for the feed).
    """
    activity = frappe.qb.DocType("Activity")
    query = frappe.qb.from_(activity).select(Count("*").as_("total"))

    for fieldname, operator, value in filters:
        column = activity[fieldname]
        if operator == ">=":
            query = query.where(column >= value)
        elif operator == "<=":
            query = query.where(column <= value)
        else:
            query = query.where(column == value)

    or_condition = None
    for fieldname, _operator, value in or_filters:
        clause = activity[fieldname].like(value)
        or_condition = clause if or_condition is None else (or_condition | clause)
    if or_condition is not None:
        query = query.where(or_condition)

    result = query.run(as_dict=True)
    return (result[0].get("total") if result else 0) or 0


def get_activities(
    organization: str,
    *,
    page: int = 1,
    page_size: int = 30,
    search: str = "",
    event_type: str | None = None,
    action_group: str | None = None,
    actor: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order: str = "desc",
) -> dict:
    """Pure org-scoped query. No permission logic — callers must gate access
    (the whitelisted producer wrapper enforces Owner/Manager).

    NOTE: the reads below intentionally use `frappe.get_all`, which bypasses doctype
    permissions. That is deliberate: the `Activity` doctype grants read only to System
    Manager, but the feed must be readable by ordinary org Owners/Managers, who are
    authorized by the calling wrapper instead. Do not "fix" this by adding permission
    checks here — it would break the feed for every non-System-Manager user.
    """
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), 100)
    order = "asc" if str(order).lower() == "asc" else "desc"

    filters = [["organization", "=", organization]]
    if event_type:
        filters.append(["event_type", "=", event_type])
    if action_group:
        filters.append(["action_group", "=", action_group])
    if actor:
        filters.append(["actor", "=", actor])
    if date_from:
        filters.append(["datetime", ">=", date_from])
    if date_to:
        filters.append(["datetime", "<=", date_to])

    or_filters = None
    if search:
        like = f"%{search}%"
        or_filters = [
            ["summary", "like", like],
            ["actor_name", "like", like],
            ["actor", "like", like],
            ["target_label", "like", like],
        ]

    fields = [
        "name",
        "organization",
        "actor",
        "actor_name",
        "actor_image",
        "event_type",
        "action_group",
        "summary",
        "target_doctype",
        "target_name",
        "target_label",
        "source",
        "datetime",
    ]

    if or_filters:
        total_count = _count_with_or_filters(filters, or_filters)
    else:
        total_count = frappe.db.count("Activity", filters=filters)

    rows = frappe.get_all(
        "Activity",
        fields=fields,
        filters=filters,
        or_filters=or_filters,
        order_by=f"datetime {order}",
        start=(page - 1) * page_size,
        limit=page_size,
    )

    total_pages = max(-(-total_count // page_size), 1)

    return {
        "data": rows,
        "total_count": total_count,
        "total_pages": total_pages,
        "available_action_groups": get_available_action_groups(organization),
    }


def get_available_action_groups(organization: str) -> list:
    rows = frappe.get_all(
        "Activity",
        filters={"organization": organization},
        distinct=True,
        pluck="action_group",
    )
    return sorted([r for r in rows if r])
