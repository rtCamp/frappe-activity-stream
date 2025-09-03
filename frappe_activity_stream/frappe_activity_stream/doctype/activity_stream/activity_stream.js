frappe.ui.form.on("Activity Stream", "refresh", function (frm) {
  $(frappe.render_template("diff_view", { doc: frm.doc, data: JSON.parse(frm.doc.diff) })).appendTo(
    frm.fields_dict.diff_visual.$wrapper.empty()
  );
});
