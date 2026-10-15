frappe.ui.form.on("Flipkart Listing", {
	setup(frm) {
		frm.set_query("erpnext_item", () => ({ filters: { has_variants: 0, disabled: 0 } }));
	},

	refresh(frm) {
		if (frm.is_new() || frm.doc.erpnext_item) return;

		frm.set_intro(
			__(
				"SKU {0} is not mapped yet. Orders that contain it cannot be imported until you choose the ERPNext item it stands for.",
				[frappe.utils.escape_html(frm.doc.sku).bold()]
			),
			"orange"
		);
	},
});
