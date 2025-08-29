# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

# import frappe
import frappe
from frappe.model.document import Document


class ActivityStreamSettings(Document):
    pass


def should_log_activity(doc_type, action, user, ip_address):
    if not user:
        return False
    settings = frappe.get_single("Activity Stream Settings")
    if not settings.enabled:
        return False
    if doc_type == "User" and action in ["Login", "Logout"]:
        return True
    if doc_type == "Activity Stream":
        return False
    allow_list = settings.get("doctype_and_action") or []
    # TODO: add user and ip address based filtering
    for entry in allow_list:
        if entry.document_type == doc_type and (entry.action == "All" or entry.action == action):
            return True
    return False