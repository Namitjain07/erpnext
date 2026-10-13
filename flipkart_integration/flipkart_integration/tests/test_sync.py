import uuid
from unittest.mock import patch

import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from flipkart_integration.flipkart import inventory, order, product
from flipkart_integration.flipkart.constants import ORDER_ITEM_ID_FIELD, SHIPMENT_ID_FIELD
from flipkart_integration.tests.fakes import FakeFlipkart, make_order_item, make_shipment

COMPANY = "Wind Power LLC"
LOCATION = "LOC-TEST"


def unique(prefix: str) -> str:
	return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class FlipkartSyncTestCase(IntegrationTestCase):
	def setUp(self):
		self.fake = FakeFlipkart()
		self.abbr = frappe.get_cached_value("Company", COMPANY, "abbr")
		self.warehouse = self.make_warehouse(unique("FK Stores"))
		self.settings = self.configure()

		for module in (order, inventory, product):
			patcher = patch.object(module, "get_client", return_value=self.fake)
			patcher.start()
			self.addCleanup(patcher.stop)

	def make_warehouse(self, name: str) -> str:
		warehouse = frappe.get_doc(
			{"doctype": "Warehouse", "warehouse_name": name, "company": COMPANY}
		).insert()
		frappe.db.commit()
		return warehouse.name

	def make_item(self, sku: str | None = None) -> str:
		sku = sku or unique("FKSKU")
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": sku,
				"item_name": sku,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		).insert()
		frappe.db.commit()
		return sku

	def configure(self, **overrides):
		settings = frappe.get_doc("Flipkart Settings")
		settings.update(
			{
				"enable_flipkart": 1,
				"app_id": "app-id",
				"app_secret": "app-secret",
				"company": COMPANY,
				"default_customer": None,
				"default_warehouse": self.warehouse,
				"enable_order_sync": 1,
				"sync_orders_from": add_days(today(), -1),
				"submit_sales_orders": 1,
				"create_missing_items": 0,
				"price_basis": "Per Unit",
				"sales_taxes_template": None,
				"shipping_charge_account": None,
				"enable_inventory_sync": 1,
			}
		)
		settings.update(overrides)
		settings.set(
			"warehouse_mapping",
			[
				{
					"flipkart_location_id": LOCATION,
					"flipkart_location_name": "Test location",
					"erpnext_warehouse": self.warehouse,
				}
			],
		)
		settings.save()
		frappe.db.commit()
		return settings

	def add_shipment(self, items: list[dict] | None = None, **kwargs) -> dict:
		shipment = make_shipment(
			shipment_id=kwargs.pop("shipment_id", unique("SHIP")),
			order_id=kwargs.pop("order_id", unique("OD")),
			items=items,
			location_id=kwargs.pop("location_id", LOCATION),
		)
		self.fake.open_shipments.append(shipment)
		return shipment

	def sales_order_for(self, shipment_id: str):
		name = frappe.db.get_value("Sales Order", {SHIPMENT_ID_FIELD: shipment_id})
		return frappe.get_doc("Sales Order", name) if name else None

	def logs_for(self, shipment_id: str) -> list[dict]:
		return frappe.get_all(
			"Ecommerce Integration Log",
			filters={"integration": "Flipkart", "method": ["like", f"%{shipment_id}%"]},
			fields=["name", "status", "message"],
		)

	def sync_orders(self, **kwargs):
		order.sync_new_orders(force=True, **kwargs)


class TestFlipkartSettings(FlipkartSyncTestCase):
	def test_enabling_creates_default_customer_and_custom_fields(self):
		self.assertEqual(self.settings.default_customer, "Flipkart Customer")
		self.assertTrue(frappe.db.exists("Customer", "Flipkart Customer"))
		self.assertTrue(
			frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": SHIPMENT_ID_FIELD})
		)
		self.assertTrue(
			frappe.db.exists("Custom Field", {"dt": "Sales Order Item", "fieldname": ORDER_ITEM_ID_FIELD})
		)

	def test_a_location_cannot_be_mapped_twice(self):
		settings = frappe.get_doc("Flipkart Settings")
		settings.append(
			"warehouse_mapping",
			{
				"flipkart_location_id": LOCATION,
				"erpnext_warehouse": self.make_warehouse(unique("FK Other")),
			},
		)

		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_api_base_url_must_be_https(self):
		with self.assertRaises(frappe.ValidationError):
			self.configure(api_base_url="http://api.flipkart.net")

	def test_default_warehouse_cannot_be_a_group(self):
		group = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": unique("FK Group"),
				"company": COMPANY,
				"is_group": 1,
			}
		).insert()

		with self.assertRaises(frappe.ValidationError):
			self.configure(default_warehouse=group.name)

	def test_warehouse_mappings_are_exposed_both_ways(self):
		self.assertEqual(self.settings.get_erpnext_to_integration_wh_mapping(), {self.warehouse: LOCATION})
		self.assertEqual(self.settings.get_integration_to_erpnext_wh_mapping(), {LOCATION: self.warehouse})


class TestListingSync(FlipkartSyncTestCase):
	def listing(self, sku: str, fsn: str | None = None, **kwargs) -> dict:
		return {
			"listingId": "LST",
			"productId": fsn or unique("FSN"),
			"sku": sku,
			"product_name": f"Product {sku}",
			**kwargs,
		}

	def test_listing_is_linked_to_the_item_with_the_same_code(self):
		sku = self.make_item()
		fsn = unique("FSNLISTING")
		self.fake.listings.append(self.listing(sku, fsn=fsn))

		product.sync_listings()

		ecom_item = frappe.get_doc("Ecommerce Item", {"integration": "Flipkart", "sku": sku})
		self.assertEqual(ecom_item.erpnext_item_code, sku)
		self.assertEqual(ecom_item.integration_item_code, fsn)

	def test_syncing_twice_does_not_duplicate_the_link(self):
		sku = self.make_item()
		self.fake.listings.append(self.listing(sku))

		product.sync_listings()
		product.sync_listings()

		self.assertEqual(frappe.db.count("Ecommerce Item", {"integration": "Flipkart", "sku": sku}), 1)

	def test_unknown_sku_is_skipped_unless_missing_items_are_created(self):
		sku = unique("FKNEW")
		self.fake.listings.append(self.listing(sku))

		product.sync_listings()
		self.assertFalse(frappe.db.exists("Item", sku))

		self.configure(create_missing_items=1)
		product.sync_listings()

		self.assertEqual(frappe.db.get_value("Item", sku, "item_name"), f"Product {sku}")
		self.assertTrue(frappe.db.exists("Ecommerce Item", {"integration": "Flipkart", "sku": sku}))

	def test_inactive_listings_are_linked_too(self):
		sku = self.make_item()
		self.fake.listings.append(self.listing(sku, _state="INACTIVE"))

		product.sync_listings()

		self.assertTrue(frappe.db.exists("Ecommerce Item", {"integration": "Flipkart", "sku": sku}))


class TestOrderSync(FlipkartSyncTestCase):
	def test_shipment_becomes_a_submitted_sales_order(self):
		sku = self.make_item()
		shipment = self.add_shipment(
			items=[make_order_item(sku=sku, quantity=2, customer_price=250.0, order_item_id="OI-77")]
		)

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertIsNotNone(sales_order)
		self.assertEqual(sales_order.docstatus, 1)
		self.assertEqual(sales_order.customer, "Flipkart Customer")
		self.assertEqual(sales_order.company, COMPANY)
		self.assertEqual(sales_order.flipkart_order_id, shipment["orderItems"][0]["orderId"])
		self.assertEqual(len(sales_order.items), 1)

		row = sales_order.items[0]
		self.assertEqual((row.item_code, row.qty, row.rate), (sku, 2, 250.0))
		self.assertEqual(row.warehouse, self.warehouse)
		self.assertEqual(row.get(ORDER_ITEM_ID_FIELD), "OI-77")
		self.assertEqual(sales_order.grand_total, 500.0)
		self.assertEqual(sales_order.flipkart_total_price, 290.0)

	def test_order_items_for_the_same_sku_become_one_row(self):
		sku = self.make_item()
		shipment = self.add_shipment(
			items=[
				make_order_item(sku=sku, order_item_id="OI-1", customer_price=100.0),
				make_order_item(sku=sku, order_item_id="OI-2", customer_price=100.0),
				make_order_item(sku=sku, order_item_id="OI-3", quantity=2, customer_price=130.0),
			]
		)

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual(len(sales_order.items), 1)
		row = sales_order.items[0]
		self.assertEqual(row.qty, 4)
		self.assertEqual(row.get(ORDER_ITEM_ID_FIELD), "OI-1, OI-2, OI-3")
		self.assertEqual(sales_order.grand_total, 100 + 100 + 2 * 130)

	def test_shipping_address_is_created_for_the_order(self):
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		address = frappe.get_doc("Address", sales_order.shipping_address_name)
		self.assertEqual(address.city, "Bengaluru")
		self.assertEqual(address.pincode, "560038")
		self.assertEqual(address.address_line1, "12 MG Road")
		self.assertEqual(address.links[0].link_name, "Flipkart Customer")

	def test_a_shipment_is_never_imported_twice(self):
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])

		self.sync_orders()
		self.sync_orders()

		self.assertEqual(frappe.db.count("Sales Order", {SHIPMENT_ID_FIELD: shipment["shipmentId"]}), 1)

	def test_orders_are_left_as_drafts_when_submission_is_off(self):
		self.configure(submit_sales_orders=0)
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])

		self.sync_orders()

		self.assertEqual(self.sales_order_for(shipment["shipmentId"]).docstatus, 0)

	def test_location_warehouse_is_used_and_default_applies_to_unmapped_locations(self):
		other = self.make_warehouse(unique("FK Other"))
		self.configure(default_warehouse=other)
		mapped = self.add_shipment(items=[make_order_item(sku=self.make_item())])
		unmapped = self.add_shipment(items=[make_order_item(sku=self.make_item())], location_id="LOC-UNKNOWN")

		self.sync_orders()

		self.assertEqual(self.sales_order_for(mapped["shipmentId"]).items[0].warehouse, self.warehouse)
		self.assertEqual(self.sales_order_for(unmapped["shipmentId"]).items[0].warehouse, other)

	def test_shipping_charge_is_added_when_an_account_is_configured(self):
		account = frappe.db.get_value("Account", {"company": COMPANY, "root_type": "Income", "is_group": 0})
		self.configure(shipping_charge_account=account)
		shipment = self.add_shipment(
			items=[make_order_item(sku=self.make_item(), customer_price=100.0, shipping=40.0)]
		)

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual(sales_order.total_taxes_and_charges, 40.0)
		self.assertEqual(sales_order.grand_total, 140.0)

	def test_tax_template_is_applied(self):
		account = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Output Tax GST"})
		template = frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": unique("FK GST"),
				"company": COMPANY,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": account,
						"description": "GST",
						"rate": 18,
						"included_in_print_rate": 1,
					}
				],
			}
		).insert()
		self.configure(sales_taxes_template=template.name)
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item(), customer_price=118.0)])

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual(sales_order.grand_total, 118.0)
		self.assertAlmostEqual(sales_order.total_taxes_and_charges, 18.0, places=2)

	def test_replacement_orders_are_booked_at_zero(self):
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item(), is_replacement=True)])

		self.sync_orders()

		self.assertEqual(self.sales_order_for(shipment["shipmentId"]).grand_total, 0)

	def test_unknown_sku_fails_once_and_is_retried_until_it_exists(self):
		sku = unique("FKLATER")
		shipment = self.add_shipment(items=[make_order_item(sku=sku)])

		self.sync_orders()
		self.sync_orders()

		self.assertIsNone(self.sales_order_for(shipment["shipmentId"]))
		logs = self.logs_for(shipment["shipmentId"])
		self.assertEqual(len(logs), 1)
		self.assertEqual(logs[0].status, "Error")
		self.assertIn(sku, logs[0].message)

		self.make_item(sku)
		self.sync_orders()

		self.assertIsNotNone(self.sales_order_for(shipment["shipmentId"]))
		self.assertEqual({log.status for log in self.logs_for(shipment["shipmentId"])}, {"Success"})

	def test_one_bad_shipment_does_not_block_the_others(self):
		bad = self.add_shipment(items=[make_order_item(sku=unique("FKMISSING"))])
		good = self.add_shipment(items=[make_order_item(sku=self.make_item())])

		self.sync_orders()

		self.assertIsNone(self.sales_order_for(bad["shipmentId"]))
		self.assertIsNotNone(self.sales_order_for(good["shipmentId"]))

	def test_missing_items_are_created_from_the_listing_when_allowed(self):
		sku = unique("FKAUTO")
		fsn = unique("FSNAUTO")
		self.configure(create_missing_items=1)
		self.fake.listings.append(
			{"listingId": "L", "productId": fsn, "sku": sku, "product_name": "Auto Created"}
		)
		shipment = self.add_shipment(items=[make_order_item(sku=sku, fsn=fsn)])

		self.sync_orders()

		self.assertEqual(frappe.db.get_value("Item", sku, "item_name"), "Auto Created")
		self.assertIsNotNone(self.sales_order_for(shipment["shipmentId"]))

	def test_a_sku_with_an_existing_item_is_linked_on_first_sale(self):
		sku = self.make_item()
		fsn = unique("FSNLINKED")
		shipment = self.add_shipment(items=[make_order_item(sku=sku, fsn=fsn)])

		self.sync_orders()

		self.assertEqual(
			frappe.db.get_value(
				"Ecommerce Item", {"integration": "Flipkart", "sku": sku}, "integration_item_code"
			),
			fsn,
		)
		self.assertIsNotNone(self.sales_order_for(shipment["shipmentId"]))

	def test_disabled_order_sync_imports_nothing(self):
		self.configure(enable_order_sync=0)
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])

		self.sync_orders()

		self.assertIsNone(self.sales_order_for(shipment["shipmentId"]))


class TestCancellation(FlipkartSyncTestCase):
	def cancel_on_flipkart(self, shipment: dict, cancellation_type: str = "buyerCancellation"):
		self.fake.open_shipments.remove(shipment)
		self.fake.cancelled_shipments.append(
			{"shipmentId": shipment["shipmentId"], "_cancellation_type": cancellation_type}
		)

	def test_submitted_order_is_cancelled(self):
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])
		self.sync_orders()
		self.assertEqual(self.sales_order_for(shipment["shipmentId"]).docstatus, 1)

		self.cancel_on_flipkart(shipment, "sellerCancellation")
		self.sync_orders()

		self.assertEqual(self.sales_order_for(shipment["shipmentId"]).docstatus, 2)

	def test_draft_order_is_deleted(self):
		self.configure(submit_sales_orders=0)
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])
		self.sync_orders()

		self.cancel_on_flipkart(shipment, "marketplaceCancellation")
		self.sync_orders()

		self.assertIsNone(self.sales_order_for(shipment["shipmentId"]))

	def test_cancelling_twice_is_harmless(self):
		shipment = self.add_shipment(items=[make_order_item(sku=self.make_item())])
		self.sync_orders()
		self.cancel_on_flipkart(shipment)

		self.sync_orders()
		self.sync_orders()

		self.assertEqual(self.sales_order_for(shipment["shipmentId"]).docstatus, 2)

	def test_unknown_cancelled_shipment_is_ignored(self):
		self.fake.cancelled_shipments.append(
			{"shipmentId": unique("NEVER-IMPORTED"), "_cancellation_type": "buyerCancellation"}
		)

		self.sync_orders()


class TestInventorySync(FlipkartSyncTestCase):
	def linked_item_with_stock(self, qty: float = 10) -> tuple[str, str]:
		sku = self.make_item()
		self.fsn = unique("FSNSTOCK")
		self.fake.listings.append({"listingId": "L", "productId": self.fsn, "sku": sku, "product_name": sku})
		product.sync_listings()
		make_stock_entry(item_code=sku, target=self.warehouse, qty=qty, rate=100, company=COMPANY)
		frappe.db.commit()
		return sku, frappe.db.get_value("Ecommerce Item", {"integration": "Flipkart", "sku": sku})

	def sent_for(self, sku: str) -> list[dict]:
		return [call[sku] for call in self.fake.inventory_calls if sku in call]

	def test_stock_is_sent_to_the_mapped_location(self):
		sku, _ = self.linked_item_with_stock(10)

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(
			self.sent_for(sku),
			[{"product_id": self.fsn, "locations": [{"id": LOCATION, "inventory": 10}]}],
		)

	def test_unchanged_stock_is_not_sent_again(self):
		sku, _ = self.linked_item_with_stock(10)
		inventory.update_inventory_on_flipkart(force=True)

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(len(self.sent_for(sku)), 1)

	def test_a_stock_change_is_sent(self):
		sku, _ = self.linked_item_with_stock(10)
		inventory.update_inventory_on_flipkart(force=True)

		make_stock_entry(item_code=sku, target=self.warehouse, qty=5, rate=100, company=COMPANY)
		frappe.db.commit()
		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(self.sent_for(sku)[-1]["locations"][0]["inventory"], 15)

	def test_reserved_stock_is_not_offered_for_sale(self):
		sku, _ = self.linked_item_with_stock(10)
		shipment = self.add_shipment(items=[make_order_item(sku=sku, quantity=3)])
		self.sync_orders()
		self.assertIsNotNone(self.sales_order_for(shipment["shipmentId"]))

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(self.sent_for(sku)[-1]["locations"][0]["inventory"], 7)

	def test_rejected_stock_stays_pending_and_is_logged(self):
		sku, ecom_item = self.linked_item_with_stock(10)
		self.fake.inventory_ok = False

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(
			str(frappe.db.get_value("Ecommerce Item", ecom_item, "inventory_synced_on")).split(" ")[0],
			"1970-01-01",
		)
		self.assertTrue(
			frappe.db.exists(
				"Ecommerce Integration Log",
				{"integration": "Flipkart", "status": "Error", "method": inventory.METHOD},
			)
		)

		self.fake.inventory_ok = True
		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(len(self.sent_for(sku)), 2)
		self.assertFalse(
			frappe.db.exists(
				"Ecommerce Integration Log",
				{"integration": "Flipkart", "status": "Error", "method": inventory.METHOD},
			)
		)

	def test_stock_sync_can_be_switched_off(self):
		sku, _ = self.linked_item_with_stock(10)
		self.configure(enable_inventory_sync=0)

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(self.sent_for(sku), [])
