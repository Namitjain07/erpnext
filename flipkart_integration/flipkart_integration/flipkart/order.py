import json
from dataclasses import asdict

import frappe
from ecommerce_integrations.controllers.scheduling import need_to_run
from erpnext.accounts.services.taxes import get_taxes_and_charges
from frappe import _
from frappe.utils import add_days, getdate, now, now_datetime

from flipkart_integration.flipkart.api_client import FlipkartAPIError, FlipkartClient
from flipkart_integration.flipkart.constants import (
	CANCELLATION_LOOKBACK_DAYS,
	CANCELLATION_TYPES,
	ORDER_ID_FIELD,
	ORDER_ITEM_ID_FIELD,
	ORDER_TOTAL_FIELD,
	POST_DISPATCH_STATES,
	PRE_DISPATCH_STATES,
	SETTINGS_DOCTYPE,
	SHIPMENT_ID_FIELD,
)
from flipkart_integration.flipkart.mapping import FlipkartOrder, build_shipment_filter, normalize_shipment
from flipkart_integration.flipkart.product import resolve_item_code
from flipkart_integration.flipkart.utils import (
	get_client,
	get_settings,
	log_sync_error,
	resolve_sync_errors,
)

METHOD = "flipkart_integration.flipkart.order"
OLDEST_FIRST = {"field": "orderDate", "order": "asc"}


def sync_new_orders(force: bool = False, include_dispatched: bool = False) -> None:
	"""Import Flipkart shipments as Sales Orders, and cancel the ones Flipkart has cancelled.

	Runs from the scheduler every few minutes and does work only once the configured frequency has
	passed. Shipments that fail are retried on the next run because they are still not in ERPNext.
	"""
	settings = get_settings()
	if not settings.is_enabled() or not settings.enable_order_sync:
		return

	if force:
		frappe.db.set_value(SETTINGS_DOCTYPE, None, "last_order_sync", now(), update_modified=False)
	elif not need_to_run(SETTINGS_DOCTYPE, "order_sync_frequency", "last_order_sync"):
		return

	client = get_client(settings)
	try:
		import_open_orders(settings, client, include_dispatched)
		cancel_cancelled_orders(client)
	except FlipkartAPIError as exc:
		log_sync_error(f"{METHOD}.sync_new_orders", exc)
		return

	resolve_sync_errors(f"{METHOD}.sync_new_orders")
	frappe.db.commit()


def import_open_orders(settings, client: FlipkartClient, include_dispatched: bool = False) -> int:
	order_from = getdate(settings.sync_orders_from)
	filters = [build_shipment_filter("preDispatch", PRE_DISPATCH_STATES, order_from=order_from)]
	if include_dispatched:
		filters.append(build_shipment_filter("postDispatch", POST_DISPATCH_STATES, order_from=order_from))

	pending: list[str] = []
	for shipment_filter in filters:
		for shipment in client.iter_shipments(shipment_filter, sort=OLDEST_FIRST):
			shipment_id = shipment.get("shipmentId")
			if shipment_id and shipment_id not in pending and not shipment_exists(shipment_id):
				pending.append(shipment_id)

	imported = 0
	for details in client.get_shipments(pending):
		order = normalize_shipment(details, settings.price_basis)
		if _import_shipment(settings, client, order):
			imported += 1
	return imported


def _import_shipment(settings, client: FlipkartClient, order: FlipkartOrder) -> bool:
	method = f"{METHOD}.create_sales_order:{order.shipment_id}"
	try:
		sales_order = create_sales_order(settings, client, order)
	except Exception as exc:
		log_sync_error(method, exc, request_data=json.dumps(asdict(order), default=str, indent=2))
		return False

	resolve_sync_errors(method)
	frappe.db.commit()
	return bool(sales_order)


def shipment_exists(shipment_id: str) -> bool:
	return bool(frappe.db.exists("Sales Order", {SHIPMENT_ID_FIELD: shipment_id}))


def create_sales_order(settings, client: FlipkartClient, order: FlipkartOrder) -> str | None:
	if not order.lines or shipment_exists(order.shipment_id):
		return None

	transaction_date = getdate(order.order_date) if order.order_date else getdate()
	delivery_date = (
		max(getdate(order.dispatch_by), transaction_date) if order.dispatch_by else transaction_date
	)
	address = create_shipping_address(settings, order)
	warehouse = get_order_warehouse(settings, order)

	sales_order = frappe.new_doc("Sales Order")
	sales_order.update(
		{
			"company": settings.company,
			"customer": settings.default_customer,
			"transaction_date": transaction_date,
			"delivery_date": delivery_date,
			"title": f"Flipkart {order.order_id}"[:140],
			"customer_address": address,
			"shipping_address_name": address,
			"ignore_pricing_rule": 1,
			ORDER_ID_FIELD: order.order_id,
			SHIPMENT_ID_FIELD: order.shipment_id,
			ORDER_TOTAL_FIELD: order.total_price,
		}
	)

	for item_code, row in combine_lines(settings, client, order).items():
		sales_order.append(
			"items",
			{
				"item_code": item_code,
				"qty": row["qty"],
				"rate": row["amount"] / row["qty"],
				"warehouse": warehouse,
				"delivery_date": delivery_date,
				ORDER_ITEM_ID_FIELD: ", ".join(row["order_item_ids"]),
			},
		)

	if settings.sales_taxes_template:
		sales_order.taxes_and_charges = settings.sales_taxes_template
		sales_order.extend(
			"taxes", get_taxes_and_charges("Sales Taxes and Charges Template", settings.sales_taxes_template)
		)

	if settings.shipping_charge_account and order.shipping_total:
		sales_order.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": settings.shipping_charge_account,
				"description": _("Flipkart shipping charge"),
				"tax_amount": order.shipping_total,
			},
		)

	sales_order.insert(ignore_permissions=True)
	if settings.submit_sales_orders:
		sales_order.submit()
	return sales_order.name


def combine_lines(settings, client: FlipkartClient, order: FlipkartOrder) -> dict[str, dict]:
	"""One row per item. ERPNext refuses the same item on two rows of a Sales Order unless
	Selling Settings allows it, and a shipment can hold several order items for one SKU."""
	rows: dict[str, dict] = {}
	for line in order.lines:
		item_code = resolve_item_code(settings, client, line.sku, line.fsn)
		row = rows.setdefault(item_code, {"qty": 0, "amount": 0.0, "order_item_ids": []})
		row["qty"] += line.quantity
		row["amount"] += line.rate * line.quantity
		row["order_item_ids"].append(line.order_item_id)
	return rows


def get_order_warehouse(settings, order: FlipkartOrder) -> str:
	mapped = settings.get_integration_to_erpnext_wh_mapping().get(order.location_id)
	if mapped and not frappe.db.get_value("Warehouse", mapped, "is_group"):
		return mapped

	if settings.default_warehouse:
		return settings.default_warehouse

	frappe.throw(
		_(
			"Flipkart location {0} is not mapped to a warehouse and no Default Warehouse is set in Flipkart Settings."
		).format(frappe.bold(order.location_id))
	)


def create_shipping_address(settings, order: FlipkartOrder) -> str:
	address = order.address
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": f"{order.buyer_name} - {order.shipment_id}"[:140],
			"address_type": "Shipping",
			"address_line1": address["address_line1"] or _("Not Provided"),
			"address_line2": address["address_line2"],
			"city": address["city"] or _("Not Provided"),
			"state": address["state"],
			"pincode": address["pincode"],
			"phone": address["phone"],
			"country": "India",
			"links": [{"link_doctype": "Customer", "link_name": settings.default_customer}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def cancel_cancelled_orders(client: FlipkartClient) -> int:
	since = add_days(now_datetime(), -CANCELLATION_LOOKBACK_DAYS)
	cancelled = 0

	for cancellation_type in CANCELLATION_TYPES:
		shipment_filter = build_shipment_filter(
			"cancelled", ("CANCELLED",), cancellation_type=cancellation_type, cancellation_from=since
		)
		for shipment in client.iter_shipments(shipment_filter):
			if shipment_id := shipment.get("shipmentId"):
				cancelled += cancel_sales_order(shipment_id)
	return cancelled


def cancel_sales_order(shipment_id: str) -> int:
	"""Cancel the Sales Order of a shipment Flipkart has cancelled. Returns 1 if one was cancelled."""
	row = frappe.db.get_value(
		"Sales Order",
		{SHIPMENT_ID_FIELD: shipment_id, "docstatus": ["<", 2]},
		["name", "docstatus"],
		as_dict=True,
	)
	if not row:
		return 0

	method = f"{METHOD}.cancel_sales_order:{shipment_id}"
	try:
		if row.docstatus == 0:
			frappe.delete_doc("Sales Order", row.name, ignore_permissions=True)
		else:
			sales_order = frappe.get_doc("Sales Order", row.name)
			sales_order.flags.ignore_permissions = True
			sales_order.cancel()
	except Exception as exc:
		log_sync_error(method, exc)
		return 0

	resolve_sync_errors(method)
	frappe.db.commit()
	return 1
