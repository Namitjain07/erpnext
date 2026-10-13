"""Pure translation between Flipkart payloads and the shapes the sync code works with."""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

PRICE_PER_UNIT = "Per Unit"
PRICE_PER_ORDER_ITEM = "Per Order Item"
CANCELLED_ITEM_STATUSES = frozenset({"CANCELLED"})
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9\-\s()]{5,19}$")


@dataclass
class OrderLine:
	order_item_id: str
	sku: str
	fsn: str
	quantity: int
	rate: float
	shipping_charge: float
	total_price: float
	is_replacement: bool = False


@dataclass
class FlipkartOrder:
	shipment_id: str
	order_id: str
	location_id: str
	order_date: datetime | None
	dispatch_by: datetime | None
	buyer_name: str
	address: dict[str, str]
	lines: list[OrderLine] = field(default_factory=list)

	@property
	def total_price(self) -> float:
		return sum(line.total_price for line in self.lines)

	@property
	def shipping_total(self) -> float:
		return sum(line.shipping_charge for line in self.lines)


def to_api_datetime(value: date | datetime) -> str:
	"""Flipkart reads timezone-less ISO strings as IST."""
	if isinstance(value, datetime):
		return value.replace(microsecond=0, tzinfo=None).isoformat()
	return datetime(value.year, value.month, value.day).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
	if not value:
		return None
	try:
		return datetime.fromisoformat(value)
	except ValueError:
		return None


def build_shipment_filter(
	kind: str,
	states: Iterable[str],
	*,
	order_from: date | datetime | None = None,
	cancellation_type: str | None = None,
	cancellation_from: date | datetime | None = None,
) -> dict:
	shipment_filter: dict[str, Any] = {"type": kind, "states": list(states)}
	if order_from:
		shipment_filter["orderDate"] = {"from": to_api_datetime(order_from)}
	if cancellation_type:
		shipment_filter["cancellationType"] = cancellation_type
	if cancellation_from:
		shipment_filter["cancellationDate"] = {"from": to_api_datetime(cancellation_from)}
	return shipment_filter


def normalize_shipment(shipment: dict, price_basis: str = PRICE_PER_UNIT) -> FlipkartOrder:
	items = shipment.get("orderItems") or []
	live_items = [
		item for item in items if str(item.get("status") or "").upper() not in CANCELLED_ITEM_STATUSES
	]

	buyer = shipment.get("buyerDetails") or {}
	delivery = shipment.get("deliveryAddress") or {}
	buyer_name = _full_name(buyer) or _full_name(delivery) or "Flipkart Customer"

	order_ids = sorted({str(item["orderId"]) for item in items if item.get("orderId")})
	order_dates = [parse_datetime(item.get("orderDate")) for item in items]

	return FlipkartOrder(
		shipment_id=str(shipment.get("shipmentId") or ""),
		order_id=", ".join(order_ids) or str(shipment.get("orderId") or ""),
		location_id=str(shipment.get("locationId") or ""),
		order_date=min((d for d in order_dates if d), default=None),
		dispatch_by=parse_datetime(shipment.get("dispatchByDate")),
		buyer_name=buyer_name,
		address=_normalize_address(delivery),
		lines=[_normalize_line(item, price_basis) for item in live_items],
	)


def _normalize_line(item: dict, price_basis: str) -> OrderLine:
	prices = item.get("priceComponents") or {}
	quantity = max(int(item.get("quantity") or 1), 1)
	is_replacement = bool(item.get("isReplacement"))

	paid = _number(prices.get("customerPrice"))
	if paid is None:
		paid = _number(prices.get("sellingPrice")) or 0.0
	rate = paid / quantity if price_basis == PRICE_PER_ORDER_ITEM else paid

	return OrderLine(
		order_item_id=str(item.get("orderItemId") or ""),
		sku=str(item.get("sku") or ""),
		fsn=str(item.get("fsn") or ""),
		quantity=quantity,
		rate=0.0 if is_replacement else rate,
		shipping_charge=_number(prices.get("shippingCharge")) or 0.0,
		total_price=_number(prices.get("totalPrice")) or 0.0,
		is_replacement=is_replacement,
	)


def _normalize_address(delivery: dict) -> dict[str, str]:
	phone = str(delivery.get("contactNumber") or "").strip()
	return {
		"address_line1": str(delivery.get("addressLine1") or "").strip(),
		"address_line2": " ".join(
			part for part in (delivery.get("addressLine2"), delivery.get("landmark")) if part
		).strip(),
		"city": str(delivery.get("city") or "").strip(),
		"state": str(delivery.get("stateName") or delivery.get("state") or "").strip(),
		"pincode": str(delivery.get("pincode") or "").strip(),
		"phone": phone if PHONE_PATTERN.match(phone) else "",
	}


def build_inventory_payload(rows: Iterable[dict]) -> dict[str, dict]:
	"""rows: dicts with sku, product_id, location_id and qty. Stock for the same SKU and location
	(two ERPNext warehouses mapped to one Flipkart location) is added up."""
	totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
	product_ids: dict[str, str] = {}

	for row in rows:
		totals[row["sku"]][row["location_id"]] += max(int(row["qty"]), 0)
		product_ids[row["sku"]] = row["product_id"]

	return {
		sku: {
			"product_id": product_ids[sku],
			"locations": [{"id": location, "inventory": qty} for location, qty in locations.items()],
		}
		for sku, locations in totals.items()
	}


def _full_name(person: dict) -> str:
	return " ".join(str(part).strip() for part in (person.get("firstName"), person.get("lastName")) if part)


def _number(value: Any) -> float | None:
	try:
		return None if value is None else float(value)
	except TypeError, ValueError:
		return None
