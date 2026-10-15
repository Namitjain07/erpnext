import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.utils import get_datetime

from flipkart_integration.flipkart import inventory, product
from flipkart_integration.flipkart.constants import ORDER_ITEM_ID_FIELD
from flipkart_integration.tests.fakes import make_order_item
from flipkart_integration.tests.test_sync import COMPANY, FlipkartSyncTestCase, unique


class ProductMappingTestCase(FlipkartSyncTestCase):
	def listing(self, sku: str, fsn: str | None = None, **kwargs) -> dict:
		listing = {
			"listingId": "LST",
			"productId": fsn or unique("FSN"),
			"sku": sku,
			"product_name": f"Flipkart name of {sku}",
			**kwargs,
		}
		self.fake.listings.append(listing)
		return listing

	def map_listing(self, sku: str, item_code: str | None) -> None:
		"""What a person does in the Flipkart Listing form."""
		listing = frappe.get_doc("Flipkart Listing", sku)
		listing.erpnext_item = item_code
		listing.save()
		frappe.db.commit()

	def link_of(self, sku: str):
		return frappe.db.get_value(
			"Ecommerce Item",
			{"integration": "Flipkart", "sku": sku},
			["erpnext_item_code", "integration_item_code"],
			as_dict=True,
		)

	def add_stock(self, item_code: str, qty: float = 10) -> None:
		make_stock_entry(item_code=item_code, target=self.warehouse, qty=qty, rate=100, company=COMPANY)
		frappe.db.commit()

	def sent_for(self, sku: str) -> list[dict]:
		return [call[sku] for call in self.fake.inventory_calls if sku in call]


class TestListingRecords(ProductMappingTestCase):
	def test_every_listing_is_recorded_whether_or_not_it_matches_an_item(self):
		matching = self.make_item()
		unmatched = unique("FKOTHER")
		fsn = unique("FSNMATCH")
		self.listing(matching, fsn=fsn)
		self.listing(unmatched, _state="INACTIVE")

		product.sync_listings()

		row = frappe.get_doc("Flipkart Listing", matching)
		self.assertEqual(
			(row.erpnext_item, row.mapping_source, row.fsn, row.listing_state),
			(matching, "Automatic", fsn, "ACTIVE"),
		)
		self.assertEqual(row.product_title, f"Flipkart name of {matching}")

		row = frappe.get_doc("Flipkart Listing", unmatched)
		self.assertEqual((row.erpnext_item, row.listing_state), (None, "INACTIVE"))
		self.assertIsNone(self.link_of(unmatched))

	def test_the_sync_summary_counts_listings_waiting_to_be_mapped(self):
		self.listing(unique("FKWAIT"))

		product.sync_listings()

		message = frappe.get_all(
			"Ecommerce Integration Log",
			filters={"integration": "Flipkart", "method": ["like", "%sync_listings"]},
			pluck="message",
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertIn("1 waiting to be mapped", message)

	def test_syncing_again_refreshes_the_flipkart_details(self):
		sku = unique("FKCHANGE")
		listing = self.listing(sku)
		product.sync_listings()

		listing.update(product_name="Renamed on Flipkart", _state="INACTIVE")
		product.sync_listings()

		row = frappe.get_doc("Flipkart Listing", sku)
		self.assertEqual((row.product_title, row.listing_state), ("Renamed on Flipkart", "INACTIVE"))
		self.assertEqual(frappe.db.count("Flipkart Listing", {"name": sku}), 1)

	def test_an_existing_link_made_before_the_listing_list_existed_is_adopted(self):
		sku = self.make_item()
		product._link_item(sku, "FSNOLD", sku)
		frappe.db.commit()
		self.listing(sku, fsn="FSNOLD")

		product.sync_listings()

		row = frappe.get_doc("Flipkart Listing", sku)
		self.assertEqual((row.erpnext_item, row.mapping_source), (sku, "Automatic"))
		self.assertEqual(frappe.db.count("Ecommerce Item", {"integration": "Flipkart", "sku": sku}), 1)


class TestMappingByHand(ProductMappingTestCase):
	def setUp(self):
		super().setUp()
		self.sku = unique("FKSHIRT")
		self.fsn = unique("FSNSHIRT")
		self.item = self.make_item(unique("ERPSHIRT"))
		self.listing(self.sku, fsn=self.fsn)
		product.sync_listings()

	def test_choosing_an_item_links_it_to_the_flipkart_sku(self):
		self.assertIsNone(self.link_of(self.sku))

		self.map_listing(self.sku, self.item)

		self.assertEqual(
			self.link_of(self.sku), {"erpnext_item_code": self.item, "integration_item_code": self.fsn}
		)
		self.assertEqual(frappe.db.get_value("Flipkart Listing", self.sku, "mapping_source"), "Manual")
		self.assertEqual(frappe.db.get_value("Flipkart Listing", self.sku, "item_name"), self.item)

	def test_a_manual_mapping_survives_the_next_listing_sync(self):
		self.map_listing(self.sku, self.item)

		product.sync_listings()

		self.assertEqual(self.link_of(self.sku).erpnext_item_code, self.item)
		self.assertEqual(frappe.db.get_value("Flipkart Listing", self.sku, "mapping_source"), "Manual")

	def test_a_manual_mapping_wins_over_an_item_with_the_same_code(self):
		same_code = self.make_item(unique("FKSAME"))
		other = self.make_item(unique("ERPOTHER"))
		self.listing(same_code)
		product.sync_listings()
		self.assertEqual(self.link_of(same_code).erpnext_item_code, same_code)

		self.map_listing(same_code, other)
		product.sync_listings()

		self.assertEqual(self.link_of(same_code).erpnext_item_code, other)

	def test_a_deleted_link_is_restored_from_the_manual_mapping_not_from_the_item_code(self):
		same_code = self.make_item(unique("FKSAME"))
		other = self.make_item(unique("ERPOTHER"))
		self.listing(same_code)
		product.sync_listings()
		self.map_listing(same_code, other)
		frappe.db.delete("Ecommerce Item", {"integration": "Flipkart", "sku": same_code})
		frappe.db.commit()

		product.sync_listings()

		self.assertEqual(self.link_of(same_code).erpnext_item_code, other)

	def test_a_link_that_was_changed_behind_our_back_is_put_right_by_the_sync(self):
		self.map_listing(self.sku, self.item)
		frappe.db.set_value(
			"Ecommerce Item",
			{"integration": "Flipkart", "sku": self.sku},
			"erpnext_item_code",
			self.make_item(),
		)
		frappe.db.commit()

		product.sync_listings()

		self.assertEqual(self.link_of(self.sku).erpnext_item_code, self.item)

	def test_choosing_another_item_moves_the_link(self):
		other = self.make_item(unique("ERPOTHER"))
		self.map_listing(self.sku, self.item)

		self.map_listing(self.sku, other)

		self.assertEqual(self.link_of(self.sku).erpnext_item_code, other)
		self.assertEqual(frappe.db.count("Ecommerce Item", {"integration": "Flipkart", "sku": self.sku}), 1)

	def test_clearing_the_item_removes_the_link(self):
		self.map_listing(self.sku, self.item)

		self.map_listing(self.sku, None)

		self.assertIsNone(self.link_of(self.sku))
		self.assertFalse(frappe.db.get_value("Flipkart Listing", self.sku, "mapping_source"))

	def test_clearing_the_item_returns_the_sku_to_automatic_matching(self):
		same_code = self.make_item(unique("FKAUTO"))
		other = self.make_item(unique("ERPOTHER"))
		self.listing(same_code)
		product.sync_listings()
		self.map_listing(same_code, other)

		self.map_listing(same_code, None)
		product.sync_listings()

		row = frappe.get_doc("Flipkart Listing", same_code)
		self.assertEqual((row.erpnext_item, row.mapping_source), (same_code, "Automatic"))
		self.assertEqual(self.link_of(same_code).erpnext_item_code, same_code)

	def test_deleting_the_listing_removes_the_link(self):
		self.map_listing(self.sku, self.item)

		frappe.delete_doc("Flipkart Listing", self.sku)
		frappe.db.commit()

		self.assertIsNone(self.link_of(self.sku))

	def test_a_template_item_cannot_be_chosen(self):
		template = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": unique("FKTEMPLATE"),
				"item_name": "Template",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"has_variants": 1,
				"attributes": [{"attribute": "Size"}],
			}
		)
		if not frappe.db.exists("Item Attribute", "Size"):
			frappe.get_doc(
				{
					"doctype": "Item Attribute",
					"attribute_name": "Size",
					"item_attribute_values": [{"attribute_value": "S", "abbr": "S"}],
				}
			).insert()
		template.insert()
		frappe.db.commit()

		with self.assertRaises(frappe.ValidationError):
			self.map_listing(self.sku, template.name)

	def test_a_disabled_item_cannot_be_chosen(self):
		frappe.db.set_value("Item", self.item, "disabled", 1)
		frappe.db.commit()

		with self.assertRaises(frappe.ValidationError):
			self.map_listing(self.sku, self.item)

	def test_a_product_id_can_be_mapped_to_an_item_only_once(self):
		twin = unique("FKTWIN")
		self.listing(twin, fsn=self.fsn)
		product.sync_listings()
		self.map_listing(self.sku, self.item)

		with self.assertRaises(frappe.ValidationError):
			self.map_listing(twin, self.item)
		frappe.db.rollback()

		self.assertIsNone(self.link_of(twin))

	def test_stock_managers_can_map_but_other_users_cannot(self):
		def user_with(role: str) -> str:
			email = f"{unique('mapper').lower()}@example.com"
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Mapper",
					"send_welcome_email": 0,
					"roles": [{"role": role}],
				}
			).insert()
			return email

		stock_manager, sales_user = user_with("Stock Manager"), user_with("Sales User")

		self.assertTrue(frappe.has_permission("Flipkart Listing", "write", user=stock_manager))
		self.assertFalse(frappe.has_permission("Flipkart Listing", "write", user=sales_user))
		self.assertFalse(frappe.has_permission("Flipkart Listing", "read", user=sales_user))


class TestMappedProductsInOrders(ProductMappingTestCase):
	def test_an_order_is_booked_against_the_mapped_item(self):
		sku = unique("FKNAME")
		item = self.make_item(unique("ERPNAME"))
		self.listing(sku)
		product.sync_listings()
		self.map_listing(sku, item)
		shipment = self.add_shipment(items=[make_order_item(sku=sku, quantity=2)])

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual([(row.item_code, row.qty) for row in sales_order.items], [(item, 2)])

	def test_two_flipkart_skus_for_one_item_become_one_row(self):
		item = self.make_item(unique("ERPBOTH"))
		skus = [unique("FKCOLOUR"), unique("FKCOLOUR")]
		for sku in skus:
			self.listing(sku)
		product.sync_listings()
		for sku in skus:
			self.map_listing(sku, item)
		shipment = self.add_shipment(
			items=[
				make_order_item(sku=skus[0], order_item_id="OI-1", quantity=1),
				make_order_item(sku=skus[1], order_item_id="OI-2", quantity=3),
			]
		)

		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual([(row.item_code, row.qty) for row in sales_order.items], [(item, 4)])
		self.assertEqual(sales_order.items[0].get(ORDER_ITEM_ID_FIELD), "OI-1, OI-2")

	def test_an_order_with_unmapped_skus_names_all_of_them_and_waits_for_the_mapping(self):
		first, second = unique("FKNEW"), unique("FKNEW")
		shipment = self.add_shipment(
			items=[
				make_order_item(sku=first, fsn="FSNFIRST", order_item_id="OI-1"),
				make_order_item(sku=second, fsn="FSNSECOND", order_item_id="OI-2"),
			]
		)

		self.sync_orders()
		self.sync_orders()

		self.assertIsNone(self.sales_order_for(shipment["shipmentId"]))
		logs = self.logs_for(shipment["shipmentId"])
		self.assertEqual(len(logs), 1)
		self.assertIn(first, logs[0].message)
		self.assertIn(second, logs[0].message)
		self.assertNotIn("<", logs[0].message, "the log of a repeated failure keeps no markup")

		rows = {
			row.name: row
			for row in frappe.get_all(
				"Flipkart Listing",
				filters={"name": ["in", [first, second]]},
				fields=["name", "fsn", "erpnext_item"],
			)
		}
		self.assertEqual(
			{sku: row.fsn for sku, row in rows.items()}, {first: "FSNFIRST", second: "FSNSECOND"}
		)
		self.assertFalse(any(row.erpnext_item for row in rows.values()))

		items = [self.make_item(unique("ERPLATER")), self.make_item(unique("ERPLATER"))]
		self.map_listing(first, items[0])
		self.map_listing(second, items[1])
		self.sync_orders()

		sales_order = self.sales_order_for(shipment["shipmentId"])
		self.assertEqual({row.item_code for row in sales_order.items}, set(items))
		self.assertEqual({log.status for log in self.logs_for(shipment["shipmentId"])}, {"Success"})

	def test_the_product_id_from_an_order_is_kept_when_the_listing_arrives_later(self):
		sku = unique("FKEARLY")
		fsn = unique("FSNEARLY")
		self.add_shipment(items=[make_order_item(sku=sku, fsn=fsn)])
		self.sync_orders()
		item = self.make_item(unique("ERPEARLY"))
		self.map_listing(sku, item)
		self.listing(sku, fsn=fsn)

		product.sync_listings()

		self.assertEqual(self.link_of(sku), {"erpnext_item_code": item, "integration_item_code": fsn})

	def test_a_placeholder_product_id_is_replaced_by_the_one_from_flipkart(self):
		sku = unique("FKNOFSN")
		item = self.make_item(unique("ERPNOFSN"))
		product.record_unmapped([(sku, "")])
		self.map_listing(sku, item)
		self.assertEqual(self.link_of(sku).integration_item_code, sku)

		self.listing(sku, fsn="FSNREAL")
		product.sync_listings()

		self.assertEqual(self.link_of(sku).integration_item_code, "FSNREAL")
		self.assertEqual(frappe.db.get_value("Flipkart Listing", sku, "fsn"), "FSNREAL")


class TestMappedProductsInStock(ProductMappingTestCase):
	def test_stock_of_the_mapped_item_is_sent_under_the_flipkart_sku(self):
		sku, fsn = unique("FKSTOCK"), unique("FSNSTOCK")
		item = self.make_item(unique("ERPSTOCK"))
		self.listing(sku, fsn=fsn)
		product.sync_listings()
		self.map_listing(sku, item)
		self.add_stock(item, 7)

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(
			self.sent_for(sku), [{"product_id": fsn, "locations": [{"id": "LOC-TEST", "inventory": 7}]}]
		)

	def test_every_flipkart_sku_of_an_item_is_sent_its_stock(self):
		item = self.make_item(unique("ERPSHARED"))
		skus = {unique("FKA"): unique("FSNA"), unique("FKB"): unique("FSNB")}
		for sku, fsn in skus.items():
			self.listing(sku, fsn=fsn)
		product.sync_listings()
		for sku in skus:
			self.map_listing(sku, item)
		self.add_stock(item, 5)

		inventory.update_inventory_on_flipkart(force=True)

		for sku, fsn in skus.items():
			self.assertEqual(
				self.sent_for(sku), [{"product_id": fsn, "locations": [{"id": "LOC-TEST", "inventory": 5}]}]
			)

	def test_choosing_another_item_sends_the_new_items_stock(self):
		sku, fsn = unique("FKSWAP"), unique("FSNSWAP")
		first, second = self.make_item(unique("ERPFIRST")), self.make_item(unique("ERPSECOND"))
		self.listing(sku, fsn=fsn)
		product.sync_listings()
		self.map_listing(sku, first)
		self.add_stock(first, 3)
		self.add_stock(second, 9)
		inventory.update_inventory_on_flipkart(force=True)
		self.fake.inventory_calls.clear()

		self.map_listing(sku, second)
		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(
			self.sent_for(sku), [{"product_id": fsn, "locations": [{"id": "LOC-TEST", "inventory": 9}]}]
		)

	def test_remapping_marks_the_link_as_not_yet_synced(self):
		sku = unique("FKRESET")
		first, second = self.make_item(unique("ERPFIRST")), self.make_item(unique("ERPSECOND"))
		self.listing(sku)
		product.sync_listings()
		self.map_listing(sku, first)
		name = frappe.db.get_value("Ecommerce Item", {"integration": "Flipkart", "sku": sku})
		frappe.db.set_value("Ecommerce Item", name, "inventory_synced_on", "2026-10-01 00:00:00")

		self.map_listing(sku, second)

		self.assertEqual(
			get_datetime(frappe.db.get_value("Ecommerce Item", name, "inventory_synced_on")),
			get_datetime("1970-01-01"),
		)

	def test_an_unmapped_listing_gets_no_stock(self):
		self.listing(unique("FKNOSTOCK"))
		product.sync_listings()
		self.add_stock(self.make_item(), 5)

		inventory.update_inventory_on_flipkart(force=True)

		self.assertEqual(self.fake.inventory_calls, [])
