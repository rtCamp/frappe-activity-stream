# Copyright (c) 2025, rtCamp and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestActivityStream(FrappeTestCase):
    def _row(self, **overrides):
        values = {
            "doctype": "Activity Stream",
            "user": "Administrator",
            "action": "Update",
            "datetime": frappe.utils.now_datetime(),
            "summary": "test row",
        }
        values.update(overrides)
        return frappe.get_doc(values)

    def test_row_survives_a_missing_target(self):
        """An activity row must outlive the document it describes.

        `document_name` is a Dynamic Link and rows are written through `deferred_insert`,
        so the INSERT happens when the queue drains — by then a delete event's target is
        gone and a renamed one has moved. If link validation rejects the row,
        `deferred_insert` discards it via `frappe.logger().error`, which never reaches the
        Error Log, so the event disappears with no trace and the feed just looks incomplete.

        This guards the `_validate_links` override specifically. It has to be an override
        rather than a `before_validate` hook, because `insert()` validates links *before*
        `run_before_save_methods()` runs. If a Frappe upgrade renames or re-signatures
        `_validate_links`, the override silently stops applying and this test is the only
        thing that will say so.
        """
        row = self._row(
            action="Delete",
            document_type="Note",
            document_name=f"does-not-exist-{frappe.generate_hash(length=8)}",
            summary="target already deleted",
        )
        row.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Activity Stream", row.name))

    def test_live_target_is_still_validated(self):
        """The override is scoped: a bad reference to a LIVE doctype must still be caught.

        Blanket `ignore_links` would stop validating `user` and `document_type` too, which
        is what an earlier revision did and a reviewer rightly objected to.
        """
        note = frappe.get_doc({"doctype": "Note", "title": f"activity-test-{frappe.generate_hash(length=8)}"})
        note.insert(ignore_permissions=True)

        # A real, existing target inserts normally.
        row = self._row(document_type="Note", document_name=note.name)
        row.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Activity Stream", row.name))

        # A bad `user` link is still rejected: the override only relaxes the target.
        with self.assertRaises(frappe.LinkValidationError):
            self._row(user=f"nobody-{frappe.generate_hash(length=8)}@example.com").insert(ignore_permissions=True)

    def test_no_target_is_allowed(self):
        """Events with no document target at all (a read being audited, say) must insert."""
        row = self._row(document_type=None, document_name=None, summary="no target")
        row.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Activity Stream", row.name))
