# Copyright (c) 2026, rtCamp and contributors
# For license information, please see license.txt
"""Public engine API.

Document hooks capture anything going through the document lifecycle, gated by the
allow-list in Activity Stream Settings. `log_activity()` is for writes that bypass it
(`db.set_value`, `db.sql`, `db.delete`).
"""

import json

import frappe
from frappe.query_builder.functions import Count

from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream.activity_stream import (
    get_event_details,
    remove_sensitive_data,
    resolve_extra_fields,
    writable_extras,
)
from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream_settings.activity_stream_settings import (
    get_settings_cached,
)
from frappe_activity_stream.utils import get_ip_address

DEFAULT_ACTION = "Update"
_ACTION_BY_SUFFIX = (
    (("deleted", "removed", "disconnected"), "Delete"),
    (("created", "added", "uploaded", "invited", "connected"), "Create"),
)


def _infer_action(event_type: str) -> str:
    """
    Map `media.deleted` to "Delete", `post.created` to "Create", else "Update".
    """
    suffix = (event_type or "").rsplit(".", 1)[-1]
    for suffixes, action in _ACTION_BY_SUFFIX:
        if suffix in suffixes:
            return action
    return DEFAULT_ACTION


REMAP_BATCH_SIZE = 5000
REMAP_MAX_BATCHES = 400


def _mask_sensitive(meta: dict | None) -> dict | None:
    """Mask secrets in an explicitly logged payload.

    Delegates to the doctype module's `remove_sensitive_data` so the explicit and
    hook-captured paths cannot drift apart: this used to be a separate recursive
    implementation while the hook path only checked top-level keys.
    """
    if not meta or not isinstance(meta, dict):
        return meta
    return remove_sensitive_data(meta)


def log_activity(
    event_type: str,
    *,
    action: str | None = None,
    target_doctype: str | None = None,
    target_name: str | None = None,
    target_label: str | None = None,
    summary: str | None = None,
    actor: str | None = None,
    actor_name: str | None = None,
    action_group: str | None = None,
    meta: dict | None = None,
    source: str | None = None,
    extra_fields: dict | None = None,
    **_ignored,
) -> None:
    """Record one activity event for a write that no document hook can see.

    Only for writes bypassing the document lifecycle. `extra_fields` sets
    producer-owned Custom Fields; anything the
    caller omits is filled in from the `activity_extra_fields` hooks.
    """
    if getattr(frappe.local, "_skip_activity_stream_logging", False):
        return
    try:
        settings = get_settings_cached()
        if not getattr(settings, "enabled", 0):
            return

        frappe.local._skip_activity_stream_logging = True

        if _ignored:
            frappe.log_error(
                f"log_activity({event_type!r}) ignored unknown arguments: {sorted(_ignored)}",
                "activity unknown log_activity argument",
            )

        actor = actor or frappe.session.user
        action = action or _infer_action(event_type)
        origin, path, _request_args, method, referrer = get_event_details()

        activity = frappe.get_doc(
            {
                "doctype": "Activity Stream",
                "user": actor,
                "owner": actor,
                "user_name": actor_name or frappe.db.get_value("User", actor, "full_name"),
                "action": action,
                "event_type": event_type,
                "action_group": action_group,
                "summary": summary,
                "document_type": target_doctype,
                "document_name": target_name,
                "target_label": target_label,
                "datetime": frappe.utils.now_datetime(),
                "ip_address": get_ip_address(),
                "event_origin": source or origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(_mask_sensitive(meta) or {}, default=str),
            }
        )
        resolved = resolve_extra_fields(None)
        resolved.update(extra_fields or {})
        activity.update(writable_extras(resolved))

        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"log_activity failed: {event_type}")
    finally:
        frappe.local._skip_activity_stream_logging = False


def _count_with_or_filters(filters: list, or_filters: list) -> int:
    """DB-side COUNT for a filters + or_filters query."""
    activity = frappe.qb.DocType("Activity Stream")
    query = frappe.qb.from_(activity).select(Count("*").as_("total"))

    for field, operator, value in filters:
        if operator == "=":
            query = query.where(activity[field] == value)
        elif operator == "like":
            query = query.where(activity[field].like(value))
        elif operator == ">=":
            query = query.where(activity[field] >= value)
        elif operator == "<=":
            query = query.where(activity[field] <= value)
        elif operator == "not in":
            query = query.where(activity[field].notin(value))

    if or_filters:
        condition = None
        for field, operator, value in or_filters:
            clause = activity[field].like(value) if operator == "like" else activity[field] == value
            condition = clause if condition is None else (condition | clause)
        if condition is not None:
            query = query.where(condition)

    result = query.run(as_dict=True)
    return result[0].total if result else 0


def get_activities(
    scope: dict | None = None,
    page: int = 1,
    page_size: int = 30,
    search: str = "",
    action: str | None = None,
    action_group: str | None = None,
    event_type: str | None = None,
    actor: str | None = None,
    order: str = "desc",
    date_from: str | None = None,
    date_to: str | None = None,
    exclude_actors: list | None = None,
) -> dict:
    """Paginated feed. Permission and scoping are the caller's job."""
    action = action or action_group

    filters = [(field, "=", value) for field, value in (scope or {}).items()]
    if exclude_actors:
        filters.append(("user", "not in", list(exclude_actors)))
    if action:
        filters.append(("action_group", "=", action))
    if event_type:
        filters.append(("event_type", "=", event_type))
    if actor:
        filters.append(("user", "=", actor))
    if date_from:
        filters.append(("datetime", ">=", date_from))
    if date_to:
        filters.append(("datetime", "<=", date_to))

    or_filters = []
    if search:
        pattern = f"%{search}%"
        or_filters = [
            ("summary", "like", pattern),
            ("event_type", "like", pattern),
            ("document_name", "like", pattern),
            ("target_label", "like", pattern),
            ("user_name", "like", pattern),
        ]

    total_count = _count_with_or_filters(filters, or_filters)
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 30), 1), 100)
    direction = "asc" if str(order).lower() == "asc" else "desc"

    rows = frappe.get_all(
        "Activity Stream",
        filters=[list(f) for f in filters],
        or_filters=[list(f) for f in or_filters] or None,
        fields=[
            "name",
            "datetime",
            "summary",
            "user",
            "user_name",
            "action",
            "action_group",
            "event_type",
            "document_type",
            "document_name",
            "target_label",
            "event_origin",
        ],
        order_by=f"datetime {direction}",
        start=(page - 1) * page_size,
        page_length=page_size,
        ignore_permissions=True,
    )

    data = [
        {
            "name": row.name,
            "datetime": row.datetime,
            "summary": row.summary,
            "actor": row.user,
            "actor_name": row.user_name,
            "actor_image": None,
            "action_group": row.action_group or row.action,
            "event_type": row.event_type,
            "target_doctype": row.document_type,
            "target_name": row.document_name,
            "target_label": row.target_label or row.document_name,
            "source": row.event_origin,
            # `args` is NOT returned: it holds the request body, which would let any org
            # manager read other members' payloads. Re-add only behind the masker.
        }
        for row in rows
    ]

    try:
        actions = get_available_actions(scope, exclude_actors=exclude_actors)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "activity get_available_actions failed")
        actions = []

    return {
        "data": data,
        "total_count": total_count,
        "total_pages": max(-(-total_count // page_size), 1),
        "available_actions": actions,
        # Alias kept for existing consumers.
        "available_action_groups": actions,
    }


def get_available_actions(scope: dict | None = None, exclude_actors: list | None = None) -> list:
    """Distinct `action_group` values in scope, for the feed's filter.

    Deliberately does not fall back to `action`: the raw lifecycle verbs are not a
    useful business filter.
    """
    activity = frappe.qb.DocType("Activity Stream")
    query = frappe.qb.from_(activity).select(activity.action_group).distinct()
    for field, value in (scope or {}).items():
        query = query.where(activity[field] == value)
    if exclude_actors:
        query = query.where(activity.user.notin(list(exclude_actors)))
    rows = query.run(as_dict=True)
    return sorted({row.action_group for row in rows if row.action_group})


def _insert_activity(record: dict) -> None:
    """Write one queued row, bypassing permissions.
    """
    record = dict(record)
    record["doctype"] = "Activity Stream"
    frappe.get_doc(record).insert(ignore_permissions=True)


def flush_pending_activity(max_records: int = 5000) -> int:
    """Write out activity rows still sitting in the deferred-insert queue.

    Drains only the Activity Stream list, not `save_to_db()`, which walks every queued
    doctype and would change the flush cadence for every other app on the site.

    Also called before `remap_field`: an UPDATE cannot see
    rows still in Redis. Returns rows written. Never raises.
    """
    try:
        if not getattr(get_settings_cached(), "enabled", 0):
            return 0
    except Exception:
        return 0

    try:
        from frappe.deferred_insert import queue_prefix
    except Exception:
        return 0

    key = f"{queue_prefix}Activity Stream"
    written = 0
    discarded = 0

    try:
        while written < max_records:
            payload = frappe.cache.lpop(key)
            if not payload:
                break

            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            records = json.loads(payload)
            if isinstance(records, dict):
                records = [records]

            for record in records:
                try:
                    _insert_activity(record)
                except Exception:
                    discarded += 1
                    if discarded == 1:
                        frappe.log_error(frappe.get_traceback(), "activity flush: row discarded")
                    continue
                written += 1

            if written and written % 500 == 0:
                frappe.db.commit()  # nosemgrep
    except Exception:
        frappe.log_error(message=frappe.get_traceback(), title="activity flush_pending_activity failed")

    if discarded > 1:
        frappe.log_error(f"{discarded} queued activity rows discarded", "activity flush: rows discarded")

    if written:
        frappe.db.commit()  # nosemgrep

    return written


def remap_field(
    fieldname: str,
    old_value: str,
    new_value: str,
    *,
    remap_target_name: bool = False,
    commit_between_batches: bool = False,
) -> int:
    """Rewrite one column's value across existing activity rows.

    Returns rows updated. Never raises.
    """
    if not fieldname or not old_value or not new_value or old_value == new_value:
        return 0

    try:
        if fieldname not in {df.fieldname for df in frappe.get_meta("Activity Stream").fields}:
            return 0
    except Exception:
        return 0

    target_clause = ""
    if remap_target_name:
        target_clause = ", `document_name` = CASE WHEN `document_name` = %(old)s THEN %(new)s ELSE `document_name` END"

    try:
        updated = 0
        for _ in range(REMAP_MAX_BATCHES):
            remaining = frappe.db.count("Activity Stream", {fieldname: old_value})
            if not remaining:
                break

            statement = (
                f"UPDATE `tabActivity Stream` SET `{fieldname}` = %(new)s{target_clause} "
                f"WHERE `{fieldname}` = %(old)s LIMIT %(limit)s"
            )
            # nosemgrep
            frappe.db.sql(statement, {"old": old_value, "new": new_value, "limit": REMAP_BATCH_SIZE})
            updated += min(remaining, REMAP_BATCH_SIZE)

            if commit_between_batches:
                frappe.db.commit()  # nosemgrep
        else:
            frappe.log_error(
                f"activity remap_field hit the batch ceiling with rows still on "
                f"{fieldname}='{old_value}'; re-run remap_field()",
                "activity remap incomplete",
            )

        return updated
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"activity remap_field failed: {fieldname} {old_value} -> {new_value}")
        return 0
