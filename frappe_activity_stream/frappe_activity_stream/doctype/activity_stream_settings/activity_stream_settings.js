// Copyright (c) 2025, rtCamp and contributors
// For license information, please see license.txt

frappe.ui.form.on("Activity Stream Settings", {
    refresh(frm) {
        // Add custom button to the child table grid
        let grid_button = frm.fields_dict["doctype_and_action"].grid.add_custom_button(
            __("Import from Module"),
            function () {
                // Show dialog
                let d = new frappe.ui.Dialog({
                    title: "Import from Module",
                    fields: [
                        {
                            label: "Module",
                            fieldname: "module",
                            fieldtype: "Link",
                            options: "Module Def",
                            reqd: 1
                        },
                        {
                            label: "Action",
                            fieldname: "action",
                            fieldtype: "Select",
                            options: ["All", "Create", "Update", "Delete", "Login", "Logout", "Submit", "Cancel"],
                            default: "All",
                            reqd: 1
                        }
                    ],
                    primary_action_label: "Import DocTypes",
                    primary_action(values) {
                        if (!values.module) return;

                        frappe.call({
                            method: "frappe.client.get_list",
                            args: {
                                doctype: "DocType",
                                fields: ["name"],
                                filters: { module: values.module, istable: 0 },
                                limit_page_length: 1000
                            },
                            callback: function (r) {
                                if (r.message) {
                                    let existing_rows = frm.doc.doctype_and_action || [];
                                    let to_add = r.message
                                        .map(dt => dt.name)
                                        .filter(dt => {
                                            if (values.action === "All") {
                                                // No duplicates for any action if "All"
                                                return !existing_rows.some(row => row.document_type === dt);
                                            } else {
                                                // No duplicates for the same doctype+action
                                                return !existing_rows.some(row =>
                                                    row.document_type === dt &&
                                                    (row.action === values.action || row.action === "All")
                                                );
                                            }
                                        });

                                    to_add.forEach(dt => {
                                        let child = frm.add_child("doctype_and_action");
                                        child.document_type = dt;
                                        child.action = values.action;
                                        child.module = values.module;
                                    });

                                    if (to_add.length) {
                                        frm.refresh_field("doctype_and_action");
                                        frappe.show_alert({
                                            message: __("Imported {0} DocTypes", [to_add.length]),
                                            indicator: "green"
                                        });
                                    } else {
                                        frappe.show_alert({
                                            message: __("No new DocTypes to import."),
                                            indicator: "orange"
                                        });
                                    }
                                    d.hide();
                                }
                            }
                        });
                    }
                });
                d.show();
            }
        );
        grid_button.addClass('order-1');

        frm.fields_dict["keep_records_indefinitely"].df.onchange = function() {
            checkDescription();
        };

        function checkDescription() {
            if (frm.doc.keep_records_indefinitely) {
                frm.set_df_property(
                    "keep_records_indefinitely",
                    "description",
                    __("<b>Not recommended.</b> Purging old records helps to keep your Frappe installation running optimally.")
                );
            } else {
                frm.set_df_property("keep_records_indefinitely", "description", "");
            }
        }

        checkDescription();
    }
});
