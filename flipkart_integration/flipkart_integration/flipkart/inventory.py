from collections import defaultdict

import frappe
from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	get_inventory_levels_of_group_warehouse,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from frappe import _
from frappe.utils import cint, now

from flipkart_integration.flipkart.api_client import FlipkartAPIError, FlipkartClient
from flipkart_integration.flipkart.constants import MODULE_NAME, SETTINGS_DOCTYPE
from flipkart_integration.flipkart.mapping import build_inventory_payload
from flipkart_integration.flipkart.utils import (
	get_client,
	get_settings,
	log_sync_error,
	log_sync_success,
	resolve_sync_errors,
)

METHOD = "flipkart_integration.flipkart.inventory.update_inventory_on_flipkart"


def update_inventory_on_flipkart(force: bool = False, client: FlipkartClient | None = None) -> None:
	"""Send the available stock (actual less reserved) of every warehouse that changed since the
	last send to its mapped Flipkart location.

	Called by the scheduler every few minutes; does work only once the configured frequency has passed.
	"""
	settings = get_settings()
	if not settings.is_enabled() or not settings.enable_inventory_sync:
		return

	if force:
		frappe.db.set_value(SETTINGS_DOCTYPE, None, "last_inventory_sync", now(), update_modified=False)
	elif not need_to_run(SETTINGS_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
		return

	# Taken before reading the bins so a change made while we send is picked up next time.
	synced_on = now()
	rows = _collect_stock_rows(settings)
	if not rows:
		return

	client = client or get_client(settings)
	try:
		results = client.update_inventory(build_inventory_payload(rows))
	except FlipkartAPIError as exc:
		log_sync_error(METHOD, exc)
		return

	_record_results(rows, results, synced_on)


def _collect_stock_rows(settings) -> list[dict]:
	rows: list[dict] = []

	for warehouse, location_id in settings.get_erpnext_to_integration_wh_mapping().items():
		if cint(frappe.db.get_value("Warehouse", warehouse, "is_group")):
			levels = get_inventory_levels_of_group_warehouse(warehouse=warehouse, integration=MODULE_NAME)
		else:
			levels = get_inventory_levels(warehouses=(warehouse,), integration=MODULE_NAME)

		rows.extend(
			{
				"ecom_item": level.ecom_item,
				"product_id": level.integration_item_code,
				"location_id": location_id,
				"qty": cint(level.actual_qty) - cint(level.reserved_qty),
			}
			for level in levels
		)

	skus = dict(
		frappe.get_all(
			"Ecommerce Item",
			filters={"name": ["in", list({row["ecom_item"] for row in rows})]},
			fields=["name", "sku"],
			as_list=True,
		)
	)
	for row in rows:
		row["sku"] = skus.get(row["ecom_item"])

	return [row for row in rows if row["sku"]]


def _record_results(rows: list[dict], results: dict, synced_on: str) -> None:
	failures: dict[str, str] = {}
	succeeded: dict[str, bool] = defaultdict(lambda: True)

	for row in rows:
		result = results[row["sku"]]
		succeeded[row["ecom_item"]] = succeeded[row["ecom_item"]] and result.ok
		if not result.ok:
			failures[row["sku"]] = result.message

	for ecom_item, ok in succeeded.items():
		if ok:
			update_inventory_sync_status(ecom_item, synced_on)
	frappe.db.commit()

	if failures:
		details = "\n".join(f"{sku}: {message}" for sku, message in failures.items())
		log_sync_error(
			METHOD,
			Exception(
				_("Flipkart did not accept stock for {0} of {1} SKUs.").format(len(failures), len(results))
				+ "\n"
				+ details
			),
		)
	else:
		resolve_sync_errors(METHOD)
		log_sync_success(METHOD, _("Sent stock for {0} SKUs to Flipkart.").format(len(results)))
