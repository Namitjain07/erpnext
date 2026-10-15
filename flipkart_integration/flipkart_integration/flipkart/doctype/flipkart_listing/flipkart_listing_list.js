frappe.listview_settings["Flipkart Listing"] = {
	add_fields: ["erpnext_item"],

	get_indicator(doc) {
		if (!doc.erpnext_item) return [__("Not mapped"), "orange", "erpnext_item,is,not set"];
		return [__("Mapped"), "green", "erpnext_item,is,set"];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Fetch Listings from Flipkart"), () =>
			frappe.xcall("flipkart_integration.flipkart.doctype.flipkart_listing.flipkart_listing.fetch_listings")
		);
	},
};
