import time

import frappe
from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from flipkart_integration.flipkart.api_client import FlipkartClient
from flipkart_integration.flipkart.constants import (
	MODULE_NAME,
	ORDER_ID_FIELD,
	ORDER_ITEM_ID_FIELD,
	ORDER_TOTAL_FIELD,
	SETTINGS_DOCTYPE,
	SHIPMENT_ID_FIELD,
)

LOG_DOCTYPE = "Ecommerce Integration Log"
TOKEN_CACHE_KEY = "flipkart_integration:access_token"


def get_settings():
	return frappe.get_doc(SETTINGS_DOCTYPE)


def get_client(settings=None) -> FlipkartClient:
	"""Client whose access token is kept in the site cache instead of the database."""
	settings = settings or get_settings()
	cached = frappe.cache.get_value(TOKEN_CACHE_KEY) or {}
	if cached.get("app_id") != settings.app_id:
		cached = {}

	def save_token(token: str, expires_at: float) -> None:
		frappe.cache.set_value(
			TOKEN_CACHE_KEY,
			{"app_id": settings.app_id, "token": token, "expires_at": expires_at},
			expires_in_sec=max(int(expires_at - time.time()), 1),
		)

	return FlipkartClient(
		settings.app_id,
		settings.get_password("app_secret"),
		settings.api_base_url,
		token=cached.get("token"),
		token_expires_at=cached.get("expires_at"),
		on_token=save_token,
	)


def setup_custom_fields() -> None:
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": ORDER_ID_FIELD,
					"label": "Flipkart Order ID",
					"fieldtype": "Data",
					"insert_after": "title",
					"read_only": 1,
					"print_hide": 1,
					"no_copy": 1,
				},
				{
					"fieldname": SHIPMENT_ID_FIELD,
					"label": "Flipkart Shipment ID",
					"fieldtype": "Data",
					"insert_after": ORDER_ID_FIELD,
					"read_only": 1,
					"print_hide": 1,
					"no_copy": 1,
					"search_index": 1,
				},
				{
					"fieldname": ORDER_TOTAL_FIELD,
					"label": "Flipkart Total Price",
					"fieldtype": "Currency",
					"insert_after": SHIPMENT_ID_FIELD,
					"read_only": 1,
					"print_hide": 1,
					"no_copy": 1,
					"description": "What Flipkart says the buyer paid, shipping included. Compare with the grand total.",
				},
			],
			"Sales Order Item": [
				{
					"fieldname": ORDER_ITEM_ID_FIELD,
					"label": "Flipkart Order Item IDs",
					"fieldtype": "Small Text",
					"insert_after": "item_code",
					"read_only": 1,
					"print_hide": 1,
					"no_copy": 1,
				}
			],
		}
	)


def log_sync_error(method: str, exception: Exception, request_data=None) -> None:
	"""Record a failure under `method`. A failure that repeats on every scheduler run updates
	the same log instead of adding one per run."""
	frappe.db.rollback()

	existing = frappe.db.get_value(
		LOG_DOCTYPE, {"integration": MODULE_NAME, "status": "Error", "method": method}
	)
	if not existing:
		create_log(
			module_def=MODULE_NAME,
			status="Error",
			exception=exception,
			method=method,
			request_data=request_data,
			make_new=True,
		)
		return

	frappe.db.set_value(
		LOG_DOCTYPE,
		existing,
		{"message": str(exception)[:140], "traceback": frappe.get_traceback()},
	)
	frappe.db.commit()


def log_sync_success(method: str, message: str, request_data=None) -> None:
	create_log(
		module_def=MODULE_NAME,
		status="Success",
		method=method,
		message=message,
		request_data=request_data,
		make_new=True,
	)


def resolve_sync_errors(method: str) -> None:
	frappe.db.set_value(
		LOG_DOCTYPE, {"integration": MODULE_NAME, "status": "Error", "method": method}, "status", "Success"
	)
