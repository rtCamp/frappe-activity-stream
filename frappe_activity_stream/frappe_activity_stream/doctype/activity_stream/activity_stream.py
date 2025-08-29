# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
from frappe_activity_stream.frappe_activity_stream.doctype.activity_stream_settings.activity_stream_settings import should_log_activity

import json


class ActivityStream(Document):
    pass


def get_event_details():
    """
        Returns a tuple of (Event Origin, API Method or Background Job Link, API or Background Job Args) if the event
        Event Origin can either be None, "Desk", "API" or "Background Job"
        For Desk events, Method and Args will be None
        For None events, all three values will be None
    """
    request = frappe.local.request
    if request:
        # Event from Desk
        if request.path.startswith("/app/"):
            return "Desk", None, None
        # Event from API
        elif request.path.startswith("/api/"):
            try:
                args = json.loads(request.get_data() or "{}")
            except Exception:
                args = {}
            return "API Call", request.path, args

    if getattr(frappe.local, "is_background", False) or getattr(frappe.local, "is_scheduler", False):
        # Get the job name from the current request
        job_name = getattr(frappe.local, "job_name", None)
        if not job_name and getattr(frappe.local, "current_request", None):
            job_name = frappe.local.current_request.path
        return "Background Job", job_name, None

    return None, None, None


def log_event(doc, action):
    user = frappe.session.user
    ip_address = frappe.local.request_ip or None
    if not should_log_activity(doc.doctype, action, user, ip_address):
        return
    # check if this event is from a API call or a Background Job
    origin, path, args = get_event_details()

    doctype, docname, context = doc.doctype, doc.name, doc.doctype

    if action in ["Create", "Update"]:
        data_after = doc.as_json()
        if action == "Update":
            data_before = doc._doc_before_save.as_json() if doc._doc_before_save else {}
        else:
            data_before = {}
    elif action in ["Delete"]:
        data_before = doc.as_json()
        data_after = {}
    else:
        data_before = {}
        data_after = {}

    if frappe.get_meta(doc.doctype).issingle:
        action_mapping = {
            "Create": "Created Single {docname}",
            "Update": "Updated Single {docname}",
            "Delete": "Deleted Single {docname}",
            "Submit": "Submitted Single {docname}",
            "Cancel": "Cancelled Single {docname}",
        }
        doctype = "DocType"
        docname = doc.doctype
    else:
        action_mapping = {
            "Create": "Created {doctype} {docname}",
            "Update": "Updated {doctype} {docname}",
            "Delete": "Deleted {doctype} {docname}",
            "Submit": "Submitted {doctype} {docname}",
            "Cancel": "Cancelled {doctype} {docname}",
        }

    summary = action_mapping.get(action, "{action} {doctype} {docname}").format(doctype=doctype, docname=docname, action=action)
    activity = frappe.get_doc({
        "doctype": "Activity Stream",
        "user": user,
        "owner": user,
        "action": action,
        "ip_address": ip_address,
        "datetime": frappe.utils.now_datetime(),
        "summary": summary,
        "document_type": doctype,
        "document_name": docname,
        "data_before": data_before,
        "data_after": data_after,
        "event_origin": origin,
        "api_method": path if origin == "API Call" else None,
        "api_args": args if origin == "API Call" else None,
        "background_job": path if origin == "Background Job" else None,
        "background_job_args": args if origin == "Background Job" else None,
    })
    activity.db_insert()

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
    user = login_manager.user
    ip_address = frappe.local.request_ip or None
    if not should_log_activity("User", "Login", user, ip_address):
        return
    event_origin, path, args = get_event_details()
    activity = frappe.get_doc({
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
        "api_method": path if event_origin == "API Call" else None,
        "api_args": args if event_origin == "API Call" else None,
        "background_job": path if event_origin == "Background Job" else None,
        "background_job_args": args if event_origin == "Background Job" else None,
    })
    activity.db_insert()

def log_logout(login_manager):
    user = login_manager.user
    ip_address = frappe.local.request_ip or None
    if not should_log_activity("User", "Logout", user, ip_address):
        return

    event_origin, path, args = get_event_details()
    # insert logout records directly
    activity = frappe.get_doc({
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
        "api_method": path if event_origin == "API Call" else None,
        "api_args": args if event_origin == "API Call" else None,
        "background_job": path if event_origin == "Background Job" else None,
        "background_job_args": args if event_origin == "Background Job" else None,
    })
    activity.db_insert()
