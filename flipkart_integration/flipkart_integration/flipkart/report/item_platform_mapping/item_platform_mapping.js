frappe.query_reports["Item Platform Mapping"] = {
	filters: [
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "not_linked_on",
			label: __("Not Linked On"),
			fieldtype: "Link",
			options: "Module Def",
			get_query: () => ({ filters: { name: ["in", ["Flipkart", "Shopify", "Amazon", "Unicommerce"]] } }),
			description: __("Show the enabled items that have no link on this platform yet."),
		},
	],
};
