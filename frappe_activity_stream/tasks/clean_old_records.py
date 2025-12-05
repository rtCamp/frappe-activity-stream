import frappe


def clear_old_records():
    settings = frappe.get_single("Activity Stream Settings")
    if settings.keep_records_indefinitely:
        return
    max_age_days = settings.keep_records_for
    if not max_age_days:
        return
    cutoff_date = frappe.utils.add_days(frappe.utils.now_datetime(), -max_age_days)
    frappe.db.delete(
        "Activity Stream",
        filters={"datetime": ("<", cutoff_date)},
    )
    frappe.db.commit()  # nosemgrep
