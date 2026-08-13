import frappe

# Delete in bounded chunks: at 90-day retention a busy org can accumulate a very large
# number of rows, and a single unbounded DELETE would hold locks / bloat the transaction.
DELETE_BATCH_SIZE = 5000
MAX_BATCHES = 200


def clear_old_records():
    settings = frappe.get_single("Activity Stream Settings")
    if settings.keep_records_indefinitely:
        return
    max_age_days = settings.keep_records_for
    if not max_age_days:
        return
    cutoff_date = frappe.utils.add_days(frappe.utils.now_datetime(), -max_age_days)

    deleted = 0
    for _ in range(MAX_BATCHES):
        names = frappe.get_all(
            "Activity",
            filters={"datetime": ("<", cutoff_date)},
            pluck="name",
            limit=DELETE_BATCH_SIZE,
            order_by="datetime asc",
        )
        if not names:
            break
        frappe.db.delete("Activity", {"name": ("in", names)})
        frappe.db.commit()  # nosemgrep -- scheduled job: commit each batch so progress is durable
        deleted += len(names)
        if len(names) < DELETE_BATCH_SIZE:
            break
    else:
        frappe.log_error(
            message=(
                f"Stopped after {MAX_BATCHES} batches ({deleted} rows deleted); "
                f"more Activity rows older than {cutoff_date} remain."
            ),
            title="Activity retention purge incomplete",
        )
