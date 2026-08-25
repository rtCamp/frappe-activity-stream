# Copyright (c) 2026, rtCamp and contributors
# For license information, please see license.txt
"""Public engine API.

Activity is captured two ways, and the split matters:

1. **Document hooks** (`doc_events` in hooks.py) capture anything that goes through
   the document lifecycle: `doc.insert()`, `doc.save()`, `doc.delete()`. This is the
   default and covers most business actions for free. Which doctypes are captured is
   controlled by the allow-list in **Activity Stream Settings** (`doctype_and_action`);
   nothing is logged for a doctype that is not listed.

2. **`log_activity()` below**, for writes that bypass the lifecycle entirely:
   `frappe.db.set_value`, `frappe.db.sql`, `frappe.db.delete`. No document hook fires
   for those, so the producer app has to say what happened itself.

Producer apps should NOT import this module directly. They ship a thin local shim that
imports it lazily and defensively, so that a missing `frappe_activity_stream` install
can never break the action being logged.

Rows land in the **Activity Stream** doctype. Everything here maps onto its existing
fields; `organization` and `event_type` are the only additions.
"""

import json

import frappe
from frappe.query_builder.functions import Count

from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream.activity_stream import (
    get_event_details,
)
from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream_settings.activity_stream_settings import (
    get_settings_cached,
)
from frappe_activity_stream.utils import get_ip_address

# `action` on Activity Stream is a fixed Select, so an explicitly logged event still has
# to land on one of its values while the semantic name travels in `event_type`
# (action="Delete", event_type="media.deleted"). Rather than make every call site repeat
# that, infer it from the event name and let a caller override when the guess is wrong.
DEFAULT_ACTION = "Update"
_ACTION_BY_SUFFIX = (
    (("deleted", "removed", "disconnected"), "Delete"),
    (("created", "added", "uploaded", "invited", "connected"), "Create"),
)


def _infer_action(event_type: str) -> str:
    """Map `media.deleted` to "Delete", `post.created` to "Create", else "Update".

    Only genuine lifecycle events are mapped. A status flip such as
    `livestream.started`, `post.unpublished` or `subscription.cancelled` leaves the
    document in place, so it is an Update however final it reads.
    """
    suffix = (event_type or "").rsplit(".", 1)[-1]
    for suffixes, action in _ACTION_BY_SUFFIX:
        if suffix in suffixes:
            return action
    return DEFAULT_ACTION


REMAP_BATCH_SIZE = 5000
REMAP_MAX_BATCHES = 400


def _mask_sensitive(meta: dict | None) -> dict | None:
    """Mask sensitive keys anywhere in the payload, including nested dicts and lists."""
    if not meta or not isinstance(meta, dict):
        return meta
    try:
        sensitive = get_settings_cached().get_sensitive_keys()
    except Exception:
        sensitive = set()
    if not sensitive:
        return meta
    return _mask_value(meta, sensitive)


def _mask_value(value, sensitive):
    if isinstance(value, dict):
        return {k: ("*****" if k in sensitive else _mask_value(v, sensitive)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(item, sensitive) for item in value]
    return value


def _resolve_organization(organization: str | None):
    if organization:
        return organization
    for method in frappe.get_hooks("activity_org_resolver") or []:
        try:
            org = frappe.get_attr(method)(None)
            if org:
                return org
        except Exception:
            frappe.log_error(frappe.get_traceback(), "activity_org_resolver failed")
    return None


def log_activity(
    event_type: str,
    *,
    action: str | None = None,
    target_doctype: str | None = None,
    target_name: str | None = None,
    summary: str | None = None,
    organization: str | None = None,
    actor: str | None = None,
    actor_name: str | None = None,
    action_group: str | None = None,
    meta: dict | None = None,
    source: str | None = None,
    **_ignored,
) -> None:
    """Record one activity event for a write that no document hook can see.

    Only use this where the write bypasses the document lifecycle (`db.set_value`,
    `db.sql`, `db.delete`). If the code path calls `doc.save()`, `doc.insert()` or
    `doc.delete()`, the `doc_events` hooks already capture it and calling this too
    would log the same action twice; shape the wording with an
    `activity_summary_formatter` hook instead.

    Unlike hook-captured events this is NOT filtered through the Settings allow-list:
    the caller has already decided the event is worth recording. Only the global
    `enabled` switch applies.

    Unknown keyword arguments are accepted and ignored so that a producer app pinned to
    an older or newer engine cannot break on a signature change.

    Never raises.
    """
    if getattr(frappe.local, "_skip_activity_stream_logging", False):
        return
    try:
        settings = get_settings_cached()
        if not getattr(settings, "enabled", 0):
            return

        frappe.local._skip_activity_stream_logging = True

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
                "organization": _resolve_organization(organization),
                "summary": summary,
                "document_type": target_doctype,
                "document_name": target_name,
                "datetime": frappe.utils.now_datetime(),
                "ip_address": get_ip_address(),
                "event_origin": source or origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(_mask_sensitive(meta) or {}, default=str),
            }
        )
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        # Logging must never break the action being logged.
        frappe.log_error(frappe.get_traceback(), f"log_activity failed: {event_type}")
    finally:
        frappe.local._skip_activity_stream_logging = False


def _count_with_or_filters(filters: list, or_filters: list) -> int:
    """DB-side COUNT for a filters + or_filters query.

    `frappe.db.count` does not support or_filters, and passing an aggregate through
    `get_all(fields=...)` is not portable: the raw "count(*) as total" string is
    rejected on v16, while the v16 dict spec ({"COUNT": "*"}) is rejected on v15.
    Query Builder works on both.
    """
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
    organization: str,
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
) -> dict:
    """Org-scoped, paginated feed.

    Reads `Activity Stream` but returns the field names the consuming UI already uses,
    so storage details do not leak into the API contract. `action_group` is accepted as
    an alias for `action` for the same reason.

    Permission is the caller's job: this returns whatever org it is given.
    """
    action = action or action_group

    filters = [("organization", "=", organization)]
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
            "event_origin",
            "args",
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
            "target_label": row.document_name,
            "source": row.event_origin,
            "meta": row.args,
        }
        for row in rows
    ]

    actions = get_available_actions(organization)
    return {
        "data": data,
        "total_count": total_count,
        "total_pages": max(-(-total_count // page_size), 1),
        "available_actions": actions,
        # Kept so an existing consumer reading `available_action_groups` still works.
        "available_action_groups": actions,
    }


def get_available_actions(organization: str) -> list:
    """Distinct `action_group` values present for this org, for the feed's filter.

    Falls back to nothing rather than to `action`: the raw lifecycle verbs
    (Create/Update/Delete) are not useful as a business filter, and a row only lacks a
    group when no producer app claimed its doctype.
    """
    rows = frappe.get_all(
        "Activity Stream",
        filters={"organization": organization},
        fields=["distinct action_group as action_group"],
        ignore_permissions=True,
    )
    return sorted({row.action_group for row in rows if row.action_group})


def remap_organization(old_name: str, new_name: str, *, commit_between_batches: bool = False) -> int:
    """Repoint existing activity rows at an organization that was renamed.

    `organization` is a Data column, not a Link, so the engine needs no knowledge of the
    producer app's organization doctype. The price is that Frappe's rename cascade (which
    only rewrites Link fields) never reaches it: after a rename the feed filters on the
    new name, matches nothing, and looks as though every past activity was wiped.

    Driven entirely off `organization`, which is indexed. `document_name` is folded into
    the same statement rather than a second pass: it is not indexed, so filtering on it
    would full-scan every tenant's rows and lock them.

    `commit_between_batches` must stay False while a transaction is open that the caller
    may still need to roll back, notably inside an `after_rename` doc event where a
    commit would make a partial rename durable.

    Returns the number of rows updated. Never raises.
    """
    if not old_name or not new_name or old_name == new_name:
        return 0

    try:
        updated = 0
        for _ in range(REMAP_MAX_BATCHES):
            remaining = frappe.db.count("Activity Stream", {"organization": old_name})
            if not remaining:
                break

            frappe.db.sql(
                """UPDATE `tabActivity Stream`
                   SET `organization` = %(new)s,
                       `document_name` = CASE
                           WHEN `document_type` = 'Organization' AND `document_name` = %(old)s
                           THEN %(new)s ELSE `document_name` END
                   WHERE `organization` = %(old)s
                   LIMIT %(limit)s""",
                {"old": old_name, "new": new_name, "limit": REMAP_BATCH_SIZE},
            )
            updated += min(remaining, REMAP_BATCH_SIZE)

            if commit_between_batches:
                frappe.db.commit()  # nosemgrep
        else:
            frappe.log_error(
                f"activity remap_organization hit the batch ceiling with rows still on "
                f"'{old_name}'; re-run remap_organization('{old_name}', '{new_name}')",
                "activity remap incomplete",
            )

        return updated
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"activity remap_organization failed: {old_name} -> {new_name}")
        return 0
