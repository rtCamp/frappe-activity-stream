import frappe


def execute():
    """Add a composite index on (organization, datetime) — the primary access
    pattern for the org-scoped feed. Single-column indexes already exist via the
    doctype's search_index flags."""
    if not frappe.db.table_exists("Activity"):
        return
    frappe.db.add_index("Activity", ["organization", "datetime"], index_name="org_datetime")
