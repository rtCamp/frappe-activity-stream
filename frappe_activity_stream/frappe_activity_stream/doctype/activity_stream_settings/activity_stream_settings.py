# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

# import frappe
import frappe
import regex as re  # Using 'regex' for its faster performance compared to 're'
from frappe.model.document import Document


class ActivityStreamSettings(Document):
    def get_sensitive_keys(self):
        """
        Returns a list of sensitive keys to be masked in activity stream logs.
        """
        default_sensitive_keys = {
            "pwd",
            "password",
            "secret",
            "token",
            "api_key",
            "access_token",
        }
        user_defined_keys = set(
            key.strip() for key in (self.sensitive_keys or "").split(",") if key.strip()
        )
        return default_sensitive_keys.union(user_defined_keys)


def should_log_activity(doc_type, action, user, ip_address):
    if not user:
        return False
    settings = frappe.get_single("Activity Stream Settings")
    if not settings.enabled:
        return False
    if action == "Access":
        if not settings.get("log_access_enabled"):
            return False
        return True
    if doc_type == "User" and action in ["Login", "Logout"]:
        return True
    if doc_type == "Activity Stream":
        return False
    allow_list = settings.get("doctype_and_action") or []
    # TODO: add user and ip address based filtering
    for entry in allow_list:
        if entry.document_type == doc_type and (
            entry.action == "All" or entry.action == action
        ):
            return True
    return False


def should_log_path(path: str, method: str) -> bool:
    settings = frappe.get_single("Activity Stream Settings")
    ignore_patterns = settings.get("skip_regex_for_access_log") or ""
    type_of_requests_to_log = settings.get("type_of_requests_to_log", None)
    if type_of_requests_to_log:
        type_of_requests_to_log = type_of_requests_to_log.split(",")
        type_of_requests_to_log = [
            req_type.strip() for req_type in type_of_requests_to_log
        ]
        if method not in type_of_requests_to_log:
            return False
    ignore_patterns = [
        pattern.strip() for pattern in ignore_patterns.split("\n") if pattern.strip()
    ]
    for pattern in ignore_patterns:
        if re.search(pattern, path):
            return False
    return True
