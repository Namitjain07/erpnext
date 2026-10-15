frappe.ui.form.on("Flipkart Settings", {
	setup(frm) {
		frm.set_query("default_warehouse", () => ({ filters: { is_group: 0 } }));
		frm.set_query("erpnext_warehouse", "warehouse_mapping", () => ({
			filters: { company: frm.doc.company },
		}));
		frm.set_query("shipping_charge_account", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
	},

	refresh(frm) {
		if (!frm.doc.enable_flipkart || frm.is_dirty()) return;

		frm.add_custom_button(
			__("Map Products"),
			() => {
				frappe.route_options = { erpnext_item: ["is", "not set"] };
				frappe.set_route("List", "Flipkart Listing");
			},
			__("Actions")
		);
		frm.add_custom_button(__("Check Connection"), () => frm.call("check_connection"), __("Actions"));
		frm.add_custom_button(__("Sync Listings"), () => frm.call("sync_listings_now"), __("Actions"));
		frm.add_custom_button(__("Sync Orders"), () => frm.call("sync_orders_now"), __("Actions"));
		frm.add_custom_button(
			__("Sync Orders Including Dispatched"),
			() => frm.call("sync_orders_now", { include_dispatched: 1 }),
			__("Actions")
		);
		frm.add_custom_button(__("Send Stock"), () => frm.call("sync_stock_now"), __("Actions"));
	},
});
