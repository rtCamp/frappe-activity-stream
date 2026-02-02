# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.core.doctype.version.version import get_diff
from frappe.model.document import Document

from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream_settings.activity_stream_settings import (
    get_settings_cached,
    should_log_activity,
    should_log_path,
)
from frappe_activity_stream.utils import get_ip_address

MAX_SUMMARY_LENGTH = 40


class ActivityStream(Document):
    pass


def remove_sensitive_data(input_dict):
    """
    Remove sensitive data from args dictionary.
    """
    if not input_dict:
        return input_dict

    settings = get_settings_cached()
    sensitive_keys = settings.get_sensitive_keys()

    for key in sensitive_keys:
        if key in input_dict:
            input_dict[key] = "*****"
    return input_dict


def generate_summary(activity, is_single=False):
    """
    Generate a concise, detailed summary of the activity.
    """
    action = activity.action
    user = activity.user
    doctype = activity.document_type
    docname = activity.document_name
    diff = activity.diff
    origin = activity.event_origin

    # For single doctypes, docname is same as doctype, so use only doctype in summary
    doc_display = doctype if is_single else f"{doctype} {docname}"

    # Parse diff JSON
    try:
        diff_data = json.loads(diff) if diff else {}
    except Exception:
        diff_data = {}

    summary_parts = []

    # Handle Create, Update, Delete, Submit, Cancel
    if action == "Create":
        summary_parts.append(f"{user} created {doc_display}")
    elif action == "Delete":
        summary_parts.append(f"{user} deleted {doc_display}")
    elif action == "Submit":
        summary_parts.append(f"{user} submitted {doc_display}")
    elif action == "Cancel":
        summary_parts.append(f"{user} cancelled {doc_display}")
    elif action == "Update":
        changes = []
        # Parent field changes
        for change in diff_data.get("changed", []):
            field, old, new = change[:3]
            changes.append(
                f"{field} from '{str(old)[:MAX_SUMMARY_LENGTH]}' to '{str(new)[:MAX_SUMMARY_LENGTH]}'"
            )
        # Table field changes
        for row_change in diff_data.get("row_changed", []):
            table_field = row_change[0]
            row_idx = row_change[1]
            for field_change in row_change[3]:
                field, old, new = field_change[:3]
                changes.append(
                    f"{table_field} row {row_idx}: {field} from '{str(old)[:MAX_SUMMARY_LENGTH]}' to '{str(new)[:MAX_SUMMARY_LENGTH]}'"
                )
        # Added/removed rows
        for key in ["added", "removed"]:
            for add_rm in diff_data.get(key, []):
                field = add_rm[0]
                changes.append(f"{key} rows for {field}")

        if changes:
            summary_parts.append(
                f"{user} updated {doc_display}: " + ", ".join(changes[:3])
            )
        else:
            summary_parts.append(f"{user} updated {doc_display}")

    # API/Background Job info
    if origin == "API Call" and activity.method:
        summary_parts.append(f"via API {activity.method}")
    elif origin == "Background Job" and activity.method:
        summary_parts.append(f"via Background Job {activity.method}")

    return "; ".join(summary_parts)


def get_event_details(exclude_desk_events=True):
    """
    Returns a tuple of (Event Origin, API Method or Background Job Link, API or Background Job Args, HTTP Method, Referrer)
    Event Origin can either be None, "Desk", "API" or "Background Job"
    For Desk events, Method and Args will be None
    For None events, all five values will be None
    """
    if hasattr(frappe.local, "request") and frappe.local.request:
        request = frappe.local.request
        referrer = getattr(request, "referrer", None)
        if not referrer and hasattr(request, "headers"):
            referrer = request.headers.get("Referer")
        args = get_args_from_request(request)

        if request.path.startswith("/app/") or request.path.startswith("/desk/"):
            if exclude_desk_events:
                return "Desk", None, None, None, referrer
            else:
                return (
                    "Desk",
                    request.path,
                    remove_sensitive_data(args),
                    request.method,
                    referrer,
                )

        if request.path.startswith("/api/"):
            return (
                "API Call",
                request.path,
                remove_sensitive_data(args),
                request.method,
                referrer,
            )

        path = request.path
        return "Desk", path, remove_sensitive_data(args), request.method, referrer

    if hasattr(frappe.local, "job") and frappe.local.job:
        return (
            "Background Job",
            getattr(frappe.local.job, "job_name", None),
            remove_sensitive_data(getattr(frappe.local.job, "kwargs", None)),
            None,
            None,
        )

    return None, None, None, None, None


def de_json(input_dict):
    if not isinstance(input_dict, dict):
        return input_dict
    new_dict = {}
    for key, value in input_dict.items():
        if not isinstance(value, str):
            new_dict[key] = value
            continue
        try:
            new_dict[key] = de_json(json.loads(value))
        except Exception:
            new_dict[key] = value
    return new_dict


def get_args_from_request(request):
    try:
        # Prefer JSON body; fallback to form data, then query params
        args = {}
        if getattr(request, "is_json", False):
            args = request.get_json(silent=True) or {}
        else:
            raw = request.get_data()
            if raw:
                try:
                    args = json.loads(raw or "{}")
                except Exception:
                    pass
        if not args and request.form:
            args = request.form.to_dict(flat=True)
        if not args and request.args:
            args = request.args.to_dict(flat=True)
    except Exception:
        args = {}
    return de_json(args)


def log_access():
    """
    Wrapper for before_request hook to log access events.
    """
    try:
        user = frappe.session.user
        ip_address = get_ip_address()
        if not should_log_activity("User", "Access", user, ip_address):
            return

        event_origin, path, args, method, referrer = get_event_details(
            exclude_desk_events=False
        )

        if not path:
            return

        if not should_log_path(path, method):
            return

        activity = frappe.get_doc(
            {
                "owner": user,
                "doctype": "Activity Stream",
                "user": user,
                "action": "Access",
                "ip_address": ip_address,
                "datetime": frappe.utils.now_datetime(),
                "summary": f"User {user} accessed {path}",
                "document_type": "User",
                "document_name": user,
                "event_origin": event_origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(args, indent=4,default=str),
            }
        )
        # before deferred_insert, run before_insert hooks
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error logging access activity for {user}"
        )


def log_event(doc, action):
    try:
        user = frappe.session.user
        ip_address = get_ip_address()
        if not should_log_activity(doc.doctype, action, user, ip_address):
            return
        # check if this event is from a API call or a Background Job
        origin, path, args, method, referrer = get_event_details()

        doctype, docname = doc.doctype, doc.name

        if action in ["Create", "Update"]:
            data_after = doc
            if action == "Update":
                data_before = (
                    doc._doc_before_save
                    if doc._doc_before_save
                    else frappe.get_doc({"doctype": doctype, "name": docname})
                )
            else:
                data_before = frappe.get_doc({"doctype": doctype, "name": docname})
        elif action in ["Delete"]:
            data_before = doc
            data_after = frappe.get_doc({"doctype": doctype, "name": docname})
        else:
            data_before = None
            data_after = None

        is_single = False
        if frappe.get_meta(doctype).issingle:
            is_single = True
            docname = doctype

        diff = None
        if data_before or data_after:
            diff = get_diff(data_before, data_after)

        if diff:
            # Remove sensitive data from diff
            settings = get_settings_cached()
            sensitive_keys = settings.get_sensitive_keys()
            # Mask sensitive fields in parent changed fields
            changed_list = []
            for change in diff.get("changed", []):
                change_list = list(change)
                field = change_list[0]
                if field in sensitive_keys:
                    change_list[1] = "*****"
                    change_list[2] = "*****"
                changed_list.append(tuple(change_list))
            diff["changed"] = changed_list

            # Mask sensitive fields in table row changes
            row_changed_list = []
            for row_change in diff.get("row_changed", []):
                row_change_list = list(row_change)
                # row_change[3] is a list of field changes
                field_changes = []
                for field_change in row_change_list[3]:
                    field_change_list = list(field_change)
                    field = field_change_list[0]
                    if field in sensitive_keys:
                        field_change_list[1] = "*****"
                        field_change_list[2] = "*****"
                    field_changes.append(tuple(field_change_list))
                row_change_list[3] = field_changes
                row_changed_list.append(tuple(row_change_list))
            diff["row_changed"] = row_changed_list

        activity = frappe.get_doc(
            {
                "doctype": "Activity Stream",
                "user": user,
                "owner": user,
                "action": action,
                "ip_address": ip_address,
                "datetime": frappe.utils.now_datetime(),
                "document_type": doctype,
                "document_name": docname,
                "event_origin": origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(args, indent=4,default=str),
                "diff": frappe.as_json(diff, indent=None, separators=(",", ":"))
                if diff
                else None,
            }
        )
        activity.summary = generate_summary(activity, is_single)
        # before deferred_insert, run before_insert hooks
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error logging activity: {action} on {doc}"
        )


def log_create(doc, method):
    log_event(doc, "Create")


def log_update(doc, method):
    log_event(doc, "Update")


def log_delete(doc, method):
    log_event(doc, "Delete")


def log_submit(doc, method):
    log_event(doc, "Submit")


def log_cancel(doc, method):
    log_event(doc, "Cancel")


def log_login(login_manager):
    try:
        user = login_manager.user
        ip_address = get_ip_address()
        if not should_log_activity("User", "Login", user, ip_address):
            return
        event_origin, path, args, method, referrer = get_event_details()

        activity = frappe.get_doc(
            {
                "owner": user,
                "doctype": "Activity Stream",
                "user": user,
                "action": "Login",
                "ip_address": ip_address,
                "datetime": frappe.utils.now_datetime(),
                "summary": f"User {user} logged in",
                "document_type": "User",
                "document_name": user,
                "event_origin": event_origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(args, indent=4,default=str),
            }
        )
        # before deferred_insert, run before_insert hooks
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error logging login activity for {user}"
        )


def log_logout(login_manager):
    try:
        user = login_manager.user
        ip_address = get_ip_address()
        if not should_log_activity("User", "Logout", user, ip_address):
            return

        event_origin, path, args, method, referrer = get_event_details()
        # insert logout records directly
        activity = frappe.get_doc(
            {
                "owner": user,
                "doctype": "Activity Stream",
                "user": user,
                "action": "Logout",
                "ip_address": ip_address,
                "datetime": frappe.utils.now_datetime(),
                "summary": f"User {user} logged out",
                "document_type": "User",
                "document_name": user,
                "event_origin": event_origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(args, indent=4,default=str),
            }
        )
        # before deferred_insert, run before_insert hooks
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error logging logout activity for {user}"
        )


def activity_log_after_insert(doc, method):
    # Check if Impersonate log is inserted, if yes, log the impersonation activity in Activity Stream
    if doc.operation == "Impersonate":
        log_impersonate(doc.user)


def log_impersonate(user):
    try:
        ip_address = get_ip_address()
        if not should_log_activity("User", "Impersonate", user, ip_address):
            return

        impersonator = frappe.session.user

        event_origin, path, args, method, referrer = get_event_details()

        reason = args.get("reason", None)

        activity = frappe.get_doc(
            {
                "owner": impersonator,
                "doctype": "Activity Stream",
                "user": impersonator,
                "action": "Impersonate",
                "ip_address": ip_address,
                "datetime": frappe.utils.now_datetime(),
                "summary": f"User {impersonator} impersonated user {user}."
                + (f" Reason: {reason}" if reason else ""),
                "document_type": "User",
                "document_name": user,
                "event_origin": event_origin,
                "method": path,
                "type": method,
                "referrer": referrer,
                "args": json.dumps(args, indent=4,default=str),
            }
        )
        # before deferred_insert, run before_insert hooks
        activity.run_method("before_insert")
        activity.deferred_insert()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error logging impersonation activity for {user}"
        )
