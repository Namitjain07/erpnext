import frappe
from ecommerce_integrations.controllers.setting import (
	ERPNextWarehouse,
	IntegrationWarehouse,
	SettingController,
)
from frappe import _
from frappe.utils.nestedset import get_root_of

from flipkart_integration.flipkart.utils import get_client, setup_custom_fields

DEFAULT_CUSTOMER_NAME = "Flipkart Customer"


class FlipkartSettings(SettingController):
	def is_enabled(self) -> bool:
		return bool(self.enable_flipkart)

	def validate(self):
		self.validate_base_url()
		self.validate_warehouse_mapping()

		if not self.is_enabled():
			return

		self.ensure_default_customer()
		setup_custom_fields()

	def validate_base_url(self):
		if self.api_base_url and not self.api_base_url.startswith("https://"):
			frappe.throw(_("API Base URL must start with https://, the application secret is sent to it."))

	def validate_warehouse_mapping(self):
		seen_locations = set()
		seen_warehouses = set()

		for row in self.warehouse_mapping:
			if row.flipkart_location_id in seen_locations:
				frappe.throw(
					_("Row #{0}: Flipkart location {1} is mapped more than once.").format(
						row.idx, frappe.bold(row.flipkart_location_id)
					)
				)
			if row.erpnext_warehouse in seen_warehouses:
				frappe.throw(
					_("Row #{0}: Warehouse {1} is mapped more than once.").format(
						row.idx, frappe.bold(row.erpnext_warehouse)
					)
				)
			seen_locations.add(row.flipkart_location_id)
			seen_warehouses.add(row.erpnext_warehouse)

		if self.default_warehouse and frappe.db.get_value("Warehouse", self.default_warehouse, "is_group"):
			frappe.throw(_("Default Warehouse cannot be a group warehouse."))

	def ensure_default_customer(self):
		if self.default_customer:
			return

		if not frappe.db.exists("Customer", DEFAULT_CUSTOMER_NAME):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": DEFAULT_CUSTOMER_NAME,
					"customer_type": "Individual",
					"customer_group": self.get_customer_group(),
					"territory": get_root_of("Territory"),
				}
			).insert(ignore_permissions=True)

		self.default_customer = DEFAULT_CUSTOMER_NAME

	def get_customer_group(self) -> str:
		group = self.customer_group or frappe.db.get_single_value("Selling Settings", "customer_group")
		if group and not frappe.db.get_value("Customer Group", group, "is_group"):
			return group

		return frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or get_root_of("Customer Group")

	def get_erpnext_warehouses(self) -> list[ERPNextWarehouse]:
		return [row.erpnext_warehouse for row in self.warehouse_mapping]

	def get_erpnext_to_integration_wh_mapping(self) -> dict[ERPNextWarehouse, IntegrationWarehouse]:
		return {row.erpnext_warehouse: row.flipkart_location_id for row in self.warehouse_mapping}

	def get_integration_to_erpnext_wh_mapping(self) -> dict[IntegrationWarehouse, ERPNextWarehouse]:
		return {row.flipkart_location_id: row.erpnext_warehouse for row in self.warehouse_mapping}

	@frappe.whitelist()
	def check_connection(self):
		self.check_permission("write")
		get_client(self).check_connection()
		frappe.msgprint(_("Connected to Flipkart."), indicator="green", alert=True)

	@frappe.whitelist()
	def sync_listings_now(self):
		self.check_permission("write")
		frappe.enqueue(
			"flipkart_integration.flipkart.product.sync_listings",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
		)
		frappe.msgprint(_("Listings are being synced in the background."), alert=True)

	@frappe.whitelist()
	def sync_orders_now(self, include_dispatched: bool | int = False):
		self.check_permission("write")
		frappe.enqueue(
			"flipkart_integration.flipkart.order.sync_new_orders",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			force=True,
			include_dispatched=bool(int(include_dispatched)),
		)
		frappe.msgprint(_("Orders are being synced in the background."), alert=True)

	@frappe.whitelist()
	def sync_stock_now(self):
		self.check_permission("write")
		frappe.enqueue(
			"flipkart_integration.flipkart.inventory.update_inventory_on_flipkart",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			force=True,
		)
		frappe.msgprint(_("Stock is being sent to Flipkart in the background."), alert=True)
