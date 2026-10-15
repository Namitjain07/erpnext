from collections import Counter

import frappe
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item.ecommerce_item import (
	create_ecommerce_item,
)
from frappe import _
from frappe.utils import cstr, get_datetime, now

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
LISTING_DOCTYPE = "Flipkart Listing"
NOT_SYNCED = get_datetime("1970-01-01")


class UnmappedProductError(frappe.ValidationError):
	"""An order holds Flipkart SKUs that no ERPNext item stands for. `products` is a list of (sku, fsn)."""

	def __init__(self, products: list[tuple[str, str]]):
		self.products = products
		skus = ", ".join(frappe.bold(sku) for sku, _fsn in products)
		super().__init__(
			_(
				"No ERPNext Item is mapped to Flipkart SKU: {0}. Choose the item in Flipkart Listing (or turn on Create Missing Items in Flipkart Settings); the order is imported on the next sync."
			).format(skus)
		)


def sync_listings() -> None:
	"""Record every Flipkart listing as a Flipkart Listing and link it to its ERPNext item.

	A listing is matched to the item whose Item Code is the SKU, unless a person has mapped it by
	hand, which always wins. Listings that match nothing stay in the list, waiting to be mapped.
	"""
	settings = get_settings()
	if not settings.is_enabled():
		return

	client = get_client(settings)
	known = {
		row.name: row
		for row in frappe.get_all(
			LISTING_DOCTYPE, fields=["name", "erpnext_item", "mapping_source"], order_by="name"
		)
	}
	links = {
		row.sku: row
		for row in frappe.get_all(
			"Ecommerce Item",
			filters={"integration": MODULE_NAME, "sku": ["is", "set"]},
			fields=["sku", "erpnext_item_code as item", "integration_item_code as fsn"],
		)
	}
	counts: Counter[str] = Counter()

	try:
		for state in LISTING_STATES:
			for listing in client.iter_listings(state):
				counts[_sync_listing(settings, listing, state, known, links)] += 1
	except FlipkartAPIError as exc:
		log_sync_error(f"{METHOD}.sync_listings", exc)
		return

	log_sync_success(
		f"{METHOD}.sync_listings",
		_(
			"Listings: {0} linked, {1} created, {2} already linked, {3} waiting to be mapped, {4} failed"
		).format(counts["linked"], counts["created"], counts["exists"], counts["unmapped"], counts["failed"]),
	)


def _sync_listing(settings, listing: dict, state: str, known: dict, links: dict) -> str:
	sku = cstr(listing.get("sku")).strip()
	fsn = cstr(listing.get("productId")).strip()
	if not sku or not fsn:
		return "skipped"

	method = f"{METHOD}.sync_listing:{sku}"
	try:
		outcome = _reconcile(
			settings, sku, fsn, cstr(listing.get("product_name")).strip(), state, known, links
		)
	except Exception as exc:
		log_sync_error(method, exc, request_data=listing)
		return "failed"

	resolve_sync_errors(method)
	frappe.db.commit()
	return outcome


def _reconcile(settings, sku: str, fsn: str, title: str, state: str, known: dict, links: dict) -> str:
	row = known.get(sku)
	if row:
		frappe.db.set_value(
			LISTING_DOCTYPE,
			sku,
			{"fsn": fsn, "product_title": title, "listing_state": state, "last_seen": now()},
			update_modified=False,
		)
	else:
		_save_listing(sku, fsn, title=title, state=state)
		row = known[sku] = frappe._dict(name=sku, erpnext_item=None, mapping_source=None)

	link = links.get(sku)

	if row.erpnext_item and row.mapping_source == "Manual":
		if not link or link.item != row.erpnext_item or link.fsn != fsn:
			ensure_ecommerce_item(sku, fsn, row.erpnext_item)
			links[sku] = frappe._dict(sku=sku, item=row.erpnext_item, fsn=fsn)
		return "exists"

	if link:
		if row.erpnext_item != link.item:
			_save_listing(sku, fsn, item_code=link.item, source="Automatic")
			row.update(erpnext_item=link.item, mapping_source="Automatic")
		if link.fsn != fsn:
			ensure_ecommerce_item(sku, fsn, link.item)
			link.fsn = fsn
		return "exists"

	if frappe.db.exists("Item", sku):
		outcome = "linked"
	elif settings.create_missing_items:
		_create_item(settings, sku, fsn, title)
		outcome = "created"
	else:
		return "unmapped"

	_save_listing(sku, fsn, item_code=sku, source="Automatic")
	row.update(erpnext_item=sku, mapping_source="Automatic")
	links[sku] = frappe._dict(sku=sku, item=sku, fsn=fsn)
	return outcome


def resolve_item_code(settings, client: FlipkartClient, sku: str, fsn: str) -> str:
	"""ERPNext item for a SKU on an order, linking or creating it when this is the first sale.

	Raises UnmappedProductError when no item stands for the SKU."""
	item_code = frappe.db.get_value(
		"Ecommerce Item", {"integration": MODULE_NAME, "sku": sku}, "erpnext_item_code"
	)
	if item_code:
		return item_code

	if frappe.db.exists("Item", sku):
		_save_listing(sku, fsn or sku, item_code=sku, source="Automatic")
		return sku

	if settings.create_missing_items:
		listing = _find_listing(client, sku)
		title = cstr(listing.get("product_name")) if listing else sku
		_create_item(settings, sku, fsn or sku, title)
		_save_listing(sku, fsn or sku, title=title, item_code=sku, source="Automatic")
		return sku

	raise UnmappedProductError([(sku, fsn)])


def record_unmapped(products: list[tuple[str, str]]) -> None:
	"""Put the SKUs of a failed order into the Flipkart Listing list, so there is a row to map."""
	for sku, fsn in products:
		if not frappe.db.exists(LISTING_DOCTYPE, sku):
			_save_listing(sku, fsn or sku)
	frappe.db.commit()


def _find_listing(client: FlipkartClient, sku: str) -> dict | None:
	for state in LISTING_STATES:
		for listing in client.iter_listings(state, search_text=sku):
			if cstr(listing.get("sku")).strip() == sku:
				return listing
	return None


def _save_listing(
	sku: str,
	fsn: str,
	*,
	title: str = "",
	state: str = "",
	item_code: str | None = None,
	source: str | None = None,
) -> None:
	"""Create or update the Flipkart Listing of a SKU; saving it links the item."""
	if frappe.db.exists(LISTING_DOCTYPE, sku):
		listing = frappe.get_doc(LISTING_DOCTYPE, sku)
	else:
		listing = frappe.new_doc(LISTING_DOCTYPE)
		listing.update({"sku": sku, "fsn": fsn, "product_title": title, "listing_state": state or None})
		listing.last_seen = now()

	listing.flags.from_sync = True
	if item_code:
		listing.erpnext_item = item_code
		listing.mapping_source = source
	listing.save(ignore_permissions=True)


def ensure_ecommerce_item(sku: str, fsn: str, item_code: str) -> None:
	"""Make the Ecommerce Item of a Flipkart SKU point at `item_code`, creating it if there is none.

	That record is what the order and stock sync read. A changed item is marked as not yet synced, so
	its stock is sent to Flipkart on the next run."""
	fsn = fsn or sku
	existing = frappe.db.get_value(
		"Ecommerce Item",
		{"integration": MODULE_NAME, "sku": sku},
		["name", "erpnext_item_code", "integration_item_code"],
		as_dict=True,
	)

	clash = frappe.db.exists(
		"Ecommerce Item",
		{
			"integration": MODULE_NAME,
			"erpnext_item_code": item_code,
			"integration_item_code": fsn,
			"name": ["!=", existing.name if existing else ""],
		},
	)
	if clash:
		frappe.throw(
			_(
				"Another Flipkart SKU with the same product ID ({0}) is already mapped to Item {1}. A product ID can be mapped to an item once."
			).format(frappe.bold(fsn), frappe.bold(item_code))
		)

	if not existing:
		_link_item(item_code, fsn, sku)
		return

	changes: dict = {}
	if existing.erpnext_item_code != item_code:
		changes.update(
			{"erpnext_item_code": item_code, "item_synced_on": now(), "inventory_synced_on": NOT_SYNCED}
		)
	if existing.integration_item_code != fsn:
		changes["integration_item_code"] = fsn
	if changes:
		frappe.db.set_value("Ecommerce Item", existing.name, changes)


def remove_ecommerce_item(sku: str) -> None:
	for name in frappe.get_all(
		"Ecommerce Item", filters={"integration": MODULE_NAME, "sku": sku}, pluck="name"
	):
		frappe.delete_doc("Ecommerce Item", name, ignore_permissions=True, force=True)


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
