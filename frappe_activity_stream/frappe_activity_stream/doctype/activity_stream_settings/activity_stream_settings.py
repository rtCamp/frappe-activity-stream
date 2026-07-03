# Copyright (c) 2025, rtCamp and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

ACTIVITY_STREAM_SETTINGS_CACHE_KEY = "activity_stream_settings_cache"


class ActivityStreamSettings(Document):
    def get_sensitive_keys(self):
        """Keys to mask in the activity `meta` payload."""
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


def get_settings_cached():
    settings = frappe.cache.get_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY)
    if not settings:
        settings = frappe.get_single("Activity Stream Settings")
        frappe.cache.set_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY, settings)
    return settings


def invalidate_settings_cache(doc, method):
    frappe.cache.delete_value(ACTIVITY_STREAM_SETTINGS_CACHE_KEY)
