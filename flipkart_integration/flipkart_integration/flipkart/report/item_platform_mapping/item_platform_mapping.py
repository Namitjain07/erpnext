import frappe
from frappe import _
from frappe.utils import scrub


def execute(filters: dict | None = None):
	filters = frappe._dict(filters or {})

	links = frappe.get_all(
		"Ecommerce Item",
		filters={"erpnext_item_code": filters.item_code} if filters.item_code else {},
		fields=["erpnext_item_code", "integration", "sku", "integration_item_code"],
		order_by="erpnext_item_code, integration, sku",
	)
	platforms = sorted({link.integration for link in links} | ({filters.not_linked_on} - {None}))

	by_item: dict[str, dict[str, list[str]]] = {}
	for link in links:
		# The SKU is what a seller recognises; the platform's own product ID stands in when there is none.
		by_item.setdefault(link.erpnext_item_code, {}).setdefault(link.integration, []).append(
			link.sku or link.integration_item_code
		)

	if filters.not_linked_on:
		item_filters = {"disabled": 0, "has_variants": 0}
		if filters.item_code:
			item_filters["name"] = filters.item_code
		for item_code in frappe.get_all("Item", filters=item_filters, pluck="name"):
			by_item.setdefault(item_code, {})
		by_item = {item: seen for item, seen in by_item.items() if filters.not_linked_on not in seen}

	names = dict(
		frappe.get_all(
			"Item", filters={"name": ["in", list(by_item)]}, fields=["name", "item_name"], as_list=True
		)
	)
	data = [
		{
			"item_code": item_code,
			"item_name": names.get(item_code),
			**{scrub(platform): ", ".join(seen.get(platform, [])) for platform in platforms},
		}
		for item_code, seen in sorted(by_item.items())
	]
	return get_columns(platforms), data


def get_columns(platforms: list[str]) -> list[dict]:
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 200},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		*(
			{"label": _(platform), "fieldname": scrub(platform), "fieldtype": "Data", "width": 200}
			for platform in platforms
		),
	]
