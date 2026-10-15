import unittest
from datetime import date, datetime

from flipkart_integration.flipkart.mapping import (
	PRICE_PER_ORDER_ITEM,
	PRICE_PER_UNIT,
	build_inventory_payload,
	build_shipment_filter,
	normalize_shipment,
	parse_datetime,
	to_api_datetime,
)
from flipkart_integration.tests.fakes import make_order_item, make_shipment


class TestShipmentFilter(unittest.TestCase):
	def test_pre_dispatch_filter(self):
		result = build_shipment_filter("preDispatch", ["APPROVED"], order_from=date(2026, 10, 1))

		self.assertEqual(
			result,
			{"type": "preDispatch", "states": ["APPROVED"], "orderDate": {"from": "2026-10-01T00:00:00"}},
		)

	def test_cancelled_filter(self):
		result = build_shipment_filter(
			"cancelled",
			["CANCELLED"],
			cancellation_type="buyerCancellation",
			cancellation_from=datetime(2026, 10, 10, 8, 30, 15, 999),
		)

		self.assertEqual(result["cancellationType"], "buyerCancellation")
		self.assertEqual(result["cancellationDate"], {"from": "2026-10-10T08:30:15"})
		self.assertNotIn("orderDate", result)

	def test_api_datetime_drops_timezone_so_flipkart_reads_it_as_ist(self):
		aware = datetime.fromisoformat("2026-10-10T08:30:00+00:00")
		self.assertEqual(to_api_datetime(aware), "2026-10-10T08:30:00")

	def test_parse_datetime_handles_bad_input(self):
		self.assertIsNone(parse_datetime(None))
		self.assertIsNone(parse_datetime("not a date"))
		self.assertEqual(parse_datetime("2026-10-13T09:30:00+05:30").hour, 9)


class TestNormalizeShipment(unittest.TestCase):
	def test_basic_order(self):
		order = normalize_shipment(make_shipment())

		self.assertEqual(order.shipment_id, "SHIP-1")
		self.assertEqual(order.order_id, "OD1000001")
		self.assertEqual(order.location_id, "LOC-1")
		self.assertEqual(order.buyer_name, "Asha Rao")
		self.assertEqual(order.order_date, datetime.fromisoformat("2026-10-13T09:30:00+05:30"))
		self.assertEqual(order.dispatch_by.day, 15)
		self.assertEqual(len(order.lines), 1)

		line = order.lines[0]
		self.assertEqual((line.sku, line.fsn, line.quantity), ("SKU-A", "FSNAAAAAAAAAAAAA", 1))
		self.assertEqual(line.order_item_id, "OI-1")
		self.assertEqual(line.rate, 499.0)
		self.assertEqual(order.shipping_total, 40.0)
		self.assertEqual(order.total_price, 539.0)

	def test_address(self):
		address = normalize_shipment(make_shipment()).address

		self.assertEqual(address["address_line1"], "12 MG Road")
		self.assertEqual(address["address_line2"], "Indiranagar Near Metro")
		self.assertEqual(address["city"], "Bengaluru")
		self.assertEqual(address["state"], "Karnataka")
		self.assertEqual(address["pincode"], "560038")
		self.assertEqual(address["phone"], "9876543210")

	def test_masked_phone_number_is_dropped(self):
		shipment = make_shipment()
		shipment["deliveryAddress"]["contactNumber"] = "XXXXXX3210"

		self.assertEqual(normalize_shipment(shipment).address["phone"], "")

	def test_missing_buyer_and_address_fall_back_to_defaults(self):
		shipment = make_shipment()
		del shipment["buyerDetails"]
		del shipment["deliveryAddress"]

		order = normalize_shipment(shipment)

		self.assertEqual(order.buyer_name, "Flipkart Customer")
		self.assertEqual(order.address["address_line1"], "")

	def test_cancelled_items_are_left_out(self):
		shipment = make_shipment(
			items=[
				make_order_item(order_item_id="OI-1", sku="SKU-A"),
				make_order_item(order_item_id="OI-2", sku="SKU-B", status="CANCELLED"),
			]
		)

		order = normalize_shipment(shipment)

		self.assertEqual([line.sku for line in order.lines], ["SKU-A"])

	def test_all_items_cancelled_gives_no_lines(self):
		shipment = make_shipment(items=[make_order_item(status="CANCELLED")])
		self.assertEqual(normalize_shipment(shipment).lines, [])

	def test_replacement_orders_are_not_billed(self):
		shipment = make_shipment(items=[make_order_item(is_replacement=True)])

		line = normalize_shipment(shipment).lines[0]

		self.assertTrue(line.is_replacement)
		self.assertEqual(line.rate, 0.0)

	def test_price_basis_controls_whether_quantity_divides_the_price(self):
		shipment = make_shipment(items=[make_order_item(quantity=2, customer_price=1000.0)])

		per_unit = normalize_shipment(shipment, PRICE_PER_UNIT).lines[0]
		per_item = normalize_shipment(shipment, PRICE_PER_ORDER_ITEM).lines[0]

		self.assertEqual(per_unit.rate, 1000.0)
		self.assertEqual(per_item.rate, 500.0)

	def test_selling_price_is_used_when_customer_price_is_absent(self):
		item = make_order_item(customer_price=499.0)
		del item["priceComponents"]["customerPrice"]

		self.assertEqual(normalize_shipment(make_shipment(items=[item])).lines[0].rate, 599.0)

	def test_one_shipment_with_two_orders_lists_both_ids(self):
		shipment = make_shipment(
			items=[
				make_order_item(order_id="OD2", order_item_id="OI-1"),
				make_order_item(order_id="OD1", order_item_id="OI-2"),
			]
		)

		self.assertEqual(normalize_shipment(shipment).order_id, "OD1, OD2")


class TestInventoryPayload(unittest.TestCase):
	def test_payload_shape(self):
		payload = build_inventory_payload(
			[{"sku": "SKU-A", "product_id": "FSN1", "location_id": "L1", "qty": 7}]
		)

		self.assertEqual(
			payload, {"SKU-A": {"product_id": "FSN1", "locations": [{"id": "L1", "inventory": 7}]}}
		)

	def test_stock_for_the_same_location_is_added_up(self):
		payload = build_inventory_payload(
			[
				{"sku": "SKU-A", "product_id": "FSN1", "location_id": "L1", "qty": 3},
				{"sku": "SKU-A", "product_id": "FSN1", "location_id": "L1", "qty": 4},
				{"sku": "SKU-A", "product_id": "FSN1", "location_id": "L2", "qty": 5},
			]
		)

		self.assertEqual(
			payload["SKU-A"]["locations"],
			[{"id": "L1", "inventory": 7}, {"id": "L2", "inventory": 5}],
		)

	def test_negative_stock_is_sent_as_zero(self):
		payload = build_inventory_payload(
			[{"sku": "SKU-A", "product_id": "FSN1", "location_id": "L1", "qty": -2}]
		)

		self.assertEqual(payload["SKU-A"]["locations"][0]["inventory"], 0)
