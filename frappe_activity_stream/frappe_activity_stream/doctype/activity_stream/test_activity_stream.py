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

    def test_excluded_documents_are_left_out_of_rows_count_and_search(self):
        from frappe_activity_stream.api import get_activities

        tag = frappe.generate_hash(length=8)
        for name in (f"shown-{tag}", f"hidden-{tag}"):
            self._row(document_type="Note", document_name=name, summary=f"edited {name} {tag}").insert(
                ignore_permissions=True
            )

        feed = get_activities(search=tag, exclude_documents=[f"hidden-{tag}"])
        self.assertEqual([row["target_name"] for row in feed["data"]], [f"shown-{tag}"])
        self.assertEqual(feed["total_count"], 1)
        self.assertEqual(get_activities(search=f"hidden-{tag}", exclude_documents=[f"hidden-{tag}"])["total_count"], 0)

    def test_a_subquery_excludes_without_listing_names(self):
        from frappe_activity_stream.api import get_activities

        tag = frappe.generate_hash(length=8)
        for name in (f"shown-{tag}", f"hidden-{tag}"):
            self._row(document_type="Note", document_name=name, summary=f"edited {name} {tag}").insert(
                ignore_permissions=True
            )
        self._row(document_type=None, document_name=None, summary=f"no target {tag}").insert(ignore_permissions=True)

        activity = frappe.qb.DocType("Activity Stream")
        hidden = (
            frappe.qb.from_(activity).select(activity.document_name).where(activity.document_name == f"hidden-{tag}")
        )
        feed = get_activities(search=tag, exclude_documents=[hidden])
        # The row about no document stays: NOT IN must not drop empty fields.
        self.assertEqual(sorted(str(row["target_name"]) for row in feed["data"]), ["None", f"shown-{tag}"])
        self.assertEqual(feed["total_count"], 2)

    def test_names_and_subqueries_mixed_in_one_list(self):
        from frappe_activity_stream.api import get_activities

        tag = frappe.generate_hash(length=8)
        for name in (f"shown-{tag}", f"by-name-{tag}", f"by-query-{tag}"):
            self._row(document_type="Note", document_name=name, summary=f"edited {name} {tag}").insert(
                ignore_permissions=True
            )

        activity = frappe.qb.DocType("Activity Stream")
        by_query = (
            frappe.qb.from_(activity).select(activity.document_name).where(activity.document_name == f"by-query-{tag}")
        )
        feed = get_activities(search=tag, exclude_documents=[f"by-name-{tag}", by_query])
        self.assertEqual([row["target_name"] for row in feed["data"]], [f"shown-{tag}"])
        self.assertEqual(feed["total_count"], 1)

    def test_exclusion_items_must_be_names_or_subqueries(self):
        from frappe_activity_stream.api import get_activities

        for bad in (None, 42):
            with self.subTest(item=bad), self.assertRaises(TypeError):
                get_activities(exclude_documents=["a-name", bad])

    def test_excluding_actors_keeps_rows_with_no_user_in_rows_and_count(self):
        """``not in`` keeps empty fields, so the count agrees with the rows.

        The count used to drop rows with no ``user`` while the rows query kept them.
        """
        from frappe_activity_stream.api import get_activities

        tag = frappe.generate_hash(length=8)
        self._row(summary=f"by admin {tag}").insert(ignore_permissions=True)
        no_user = self._row(summary=f"by nobody {tag}")
        no_user.insert(ignore_permissions=True)
        frappe.db.set_value("Activity Stream", no_user.name, "user", None, update_modified=False)

        feed = get_activities(search=tag, exclude_actors=["Administrator"])
        self.assertEqual([row["name"] for row in feed["data"]], [no_user.name])
        self.assertEqual(feed["total_count"], 1)

    def test_an_unknown_filter_operator_is_refused(self):
        from frappe_activity_stream.api import _count_with_or_filters

        with self.assertRaises(ValueError):
            _count_with_or_filters([("user", "!=", "Administrator")], [])
        with self.assertRaises(ValueError):
            _count_with_or_filters([], [("user", "!=", "Administrator")])
