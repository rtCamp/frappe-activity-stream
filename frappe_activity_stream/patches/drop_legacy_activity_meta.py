import frappe


def execute():
    """Drop the legacy `meta` column on Activity.

    `meta` is a reserved attribute on Frappe Documents (`doc.meta` is the DocType
    meta object), so a field named `meta` never receives its value — the JSON
    column's CHECK(json_valid) then fails on the empty-string default on every
    insert. The field was renamed to `event_meta`; this removes the orphan column
    (and its CHECK constraint) for sites migrated before the rename.
    """
    if not frappe.db.table_exists("Activity"):
        return
    if frappe.db.has_column("Activity", "meta"):
        frappe.db.sql_ddl("ALTER TABLE `tabActivity` DROP COLUMN `meta`")
