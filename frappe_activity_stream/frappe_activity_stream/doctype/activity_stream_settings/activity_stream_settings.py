# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

# import frappe
import frappe
import regex as re  # Using 'regex' for its faster performance compared to 're'
from frappe.model.document import Document

ACTIVITY_STREAM_SETTINGS_CACHE_KEY = "activity_stream_settings_cache"

DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "pwd",
        "password",
        "secret",
        "token",
        "api_key",
        "access_token",
    }
)


class ActivityStreamSettings(Document):
    def get_sensitive_keys(self):
        """
        Returns a list of sensitive keys to be masked in activity stream logs.
        """
        user_defined_keys = set(key.strip() for key in (self.sensitive_keys or "").split(",") if key.strip())
        return set(DEFAULT_SENSITIVE_KEYS) | user_defined_keys


def get_settings_cached():
    settings = frappe.cache.get_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY)
    if not settings:
        settings = frappe.get_single("Activity Stream Settings")
        frappe.cache.set_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY, settings)
    return settings


def invalidate_settings_cache(doc, method):
    frappe.cache.delete_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY)


def should_log_activity(doc_type, action, user, ip_address):
    if not user:
        return False
    settings = get_settings_cached()
    if not settings.enabled:
        return False
    if action == "Access":
        if not settings.get("log_access_enabled"):
            return False
        if user == "Guest" and not settings.get("log_access_for_guest"):
            return False
        return True
    if doc_type == "User" and action in ["Login", "Logout", "Impersonate"]:
        return True
    if doc_type == "Activity Stream":
        return False
    allow_list = settings.get("doctype_and_action") or []
    # TODO: add user and ip address based filtering
    for entry in allow_list:
        if entry.document_type == doc_type and (entry.action == "All" or entry.action == action):
            return True
    return False


def should_log_path(path: str, method: str) -> bool:
    settings = get_settings_cached()
    ignore_patterns = settings.get("skip_regex_for_access_log") or ""
    type_of_requests_to_log = settings.get("type_of_requests_to_log", None)
    if type_of_requests_to_log and type_of_requests_to_log.strip():
        type_of_requests_to_log = type_of_requests_to_log.split(",")
        type_of_requests_to_log = [req_type.strip() for req_type in type_of_requests_to_log]
        if method not in type_of_requests_to_log:
            return False
    ignore_patterns = [pattern.strip() for pattern in ignore_patterns.split("\n") if pattern.strip()]
    for pattern in ignore_patterns:
        if re.search(pattern, path):
            return False
    return True
