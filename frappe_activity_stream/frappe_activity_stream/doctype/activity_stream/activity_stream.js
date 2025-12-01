frappe.ui.form.on("Activity Stream", "refresh", function (frm) {
  let diffData = [];
  if (frm.doc.diff) {
    try {
      diffData = JSON.parse(frm.doc.diff);
    } catch (e) {
      diffData = [];
    }
  }
  $(frappe.render_template("diff_view", { doc: frm.doc, data: diffData })).appendTo(
    frm.fields_dict.diff_visual.$wrapper.empty()
  );
});
