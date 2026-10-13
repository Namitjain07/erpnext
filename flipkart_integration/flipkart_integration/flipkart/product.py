from collections import Counter

import frappe
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item.ecommerce_item import (
	create_ecommerce_item,
)
from frappe import _
from frappe.utils import cstr, now

from flipkart_integration.flipkart.api_client import FlipkartAPIError, FlipkartClient
from flipkart_integration.flipkart.constants import LISTING_STATES, MODULE_NAME
from flipkart_integration.flipkart.utils import (
	get_client,
	get_settings,
	log_sync_error,
	log_sync_success,
	resolve_sync_errors,
)

METHOD = "flipkart_integration.flipkart.product"


def sync_listings() -> None:
	"""Link Flipkart listings to ERPNext items by SKU, so stock and orders know which item is which."""
	settings = get_settings()
	if not settings.is_enabled():
		return

	client = get_client(settings)
	counts: Counter[str] = Counter()

	try:
		for state in LISTING_STATES:
			for listing in client.iter_listings(state):
				counts[_sync_listing(settings, listing)] += 1
	except FlipkartAPIError as exc:
		log_sync_error(f"{METHOD}.sync_listings", exc)
		return

	log_sync_success(
		f"{METHOD}.sync_listings",
		_(
			"Listings: {0} linked, {1} created, {2} already linked, {3} without a matching item, {4} failed"
		).format(counts["linked"], counts["created"], counts["exists"], counts["skipped"], counts["failed"]),
	)


def _sync_listing(settings, listing: dict) -> str:
	sku = cstr(listing.get("sku")).strip()
	fsn = cstr(listing.get("productId")).strip()
	if not sku or not fsn:
		return "skipped"

	if frappe.db.exists("Ecommerce Item", {"integration": MODULE_NAME, "sku": sku}):
		return "exists"

	method = f"{METHOD}.sync_listing:{sku}"
	try:
		if frappe.db.exists("Item", sku):
			_link_item(sku, fsn, sku)
			outcome = "linked"
		elif settings.create_missing_items:
			_create_item(settings, sku, fsn, cstr(listing.get("product_name")))
			outcome = "created"
		else:
			return "skipped"
	except Exception as exc:
		log_sync_error(method, exc, request_data=listing)
		return "failed"

	resolve_sync_errors(method)
	frappe.db.commit()
	return outcome


def resolve_item_code(settings, client: FlipkartClient, sku: str, fsn: str) -> str:
	"""ERPNext item for a SKU on an order, linking or creating it when this is the first sale."""
	item_code = frappe.db.get_value(
		"Ecommerce Item", {"integration": MODULE_NAME, "sku": sku}, "erpnext_item_code"
	)
	if item_code:
		return item_code

	if frappe.db.exists("Item", sku):
		_link_item(sku, fsn or sku, sku)
		return sku

	if settings.create_missing_items:
		listing = _find_listing(client, sku)
		_create_item(settings, sku, fsn or sku, cstr(listing.get("product_name")) if listing else sku)
		return sku

	frappe.throw(
		_(
			"Flipkart SKU {0} has no matching Item. Create an Item with this Item Code, or turn on Create Missing Items in Flipkart Settings."
		).format(frappe.bold(sku))
	)


def _find_listing(client: FlipkartClient, sku: str) -> dict | None:
	for state in LISTING_STATES:
		for listing in client.iter_listings(state, search_text=sku):
			if cstr(listing.get("sku")).strip() == sku:
				return listing
	return None


def _link_item(item_code: str, fsn: str, sku: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Ecommerce Item",
			"integration": MODULE_NAME,
			"erpnext_item_code": item_code,
			"integration_item_code": fsn,
			"sku": sku,
			"has_variants": 0,
			"item_synced_on": now(),
		}
	).insert(ignore_permissions=True)


def _create_item(settings, sku: str, fsn: str, title: str) -> None:
	title = title or sku
	create_ecommerce_item(
		MODULE_NAME,
		fsn,
		{
			"item_code": sku,
			"item_name": title[:140],
			"description": title,
			"item_group": settings.item_group
			or frappe.db.get_single_value("Stock Settings", "item_group")
			or "All Item Groups",
			"stock_uom": frappe.db.get_single_value("Stock Settings", "stock_uom") or "Nos",
		},
		sku=sku,
	)
