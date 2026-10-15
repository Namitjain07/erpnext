import frappe
from frappe.desk.query_report import run

from flipkart_integration.tests.test_sync import FlipkartSyncTestCase, unique

REPORT = "Item Platform Mapping"


class TestItemPlatformMapping(FlipkartSyncTestCase):
	def link(self, item_code: str, integration: str, product_id: str, sku: str | None = None) -> None:
		frappe.get_doc(
			{
				"doctype": "Ecommerce Item",
				"integration": integration,
				"erpnext_item_code": item_code,
				"integration_item_code": product_id,
				"sku": sku,
			}
		).insert()
		frappe.db.commit()

	def setUp(self):
		super().setUp()
		self.on_two = self.make_item(unique("ERPTWO"))
		self.on_one = self.make_item(unique("ERPONE"))
		self.on_none = self.make_item(unique("ERPNONE"))
		self.flipkart_skus = [unique("FKA"), unique("FKB")]
		for sku in self.flipkart_skus:
			self.link(self.on_two, "Flipkart", unique("FSN"), sku=sku)
		self.shopify_sku = unique("SHOPSKU")
		self.unicommerce_sku = unique("UNISKU")
		self.link(self.on_two, "Shopify", unique("SHOPIFYPRODUCT"), sku=self.shopify_sku)
		self.link(self.on_one, "Unicommerce", self.unicommerce_sku)

	def report(self, **filters) -> tuple[list[str], dict[str, dict]]:
		result = run(REPORT, filters=filters, ignore_prepared_report=True)
		return [column["fieldname"] for column in result["columns"]], {
			row["item_code"]: row for row in result["result"]
		}

	def test_one_row_per_item_with_a_column_for_each_platform_it_is_on(self):
		columns, rows = self.report(item_code=self.on_two)

		self.assertEqual(columns, ["item_code", "item_name", "flipkart", "shopify"])
		self.assertEqual(list(rows), [self.on_two])
		self.assertEqual(rows[self.on_two]["flipkart"], ", ".join(self.flipkart_skus))
		self.assertEqual(rows[self.on_two]["shopify"], self.shopify_sku)

	def test_the_platforms_product_id_is_shown_when_there_is_no_sku(self):
		_, rows = self.report(item_code=self.on_one)

		self.assertEqual(rows[self.on_one]["unicommerce"], self.unicommerce_sku)

	def test_the_whole_catalogue_lists_every_linked_item_and_no_other(self):
		_, rows = self.report()

		self.assertIn(self.on_two, rows)
		self.assertIn(self.on_one, rows)
		self.assertNotIn(self.on_none, rows)

	def test_items_missing_on_a_platform_can_be_listed(self):
		columns, rows = self.report(not_linked_on="Flipkart")

		self.assertNotIn(self.on_two, rows)
		self.assertIn(self.on_one, rows)
		self.assertIn(self.on_none, rows)
		self.assertIn("flipkart", columns)
		self.assertEqual(rows[self.on_none]["flipkart"], "")

	def test_a_disabled_item_is_not_reported_as_missing(self):
		frappe.db.set_value("Item", self.on_none, "disabled", 1)
		frappe.db.commit()

		_, rows = self.report(not_linked_on="Flipkart")

		self.assertNotIn(self.on_none, rows)
