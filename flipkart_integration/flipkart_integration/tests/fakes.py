"""Test doubles and fixtures shared by the unit tests. Nothing here talks to Flipkart."""

import json
from collections import deque
from typing import Any


class FakeResponse:
	def __init__(self, status_code: int = 200, payload: Any = None, headers: dict | None = None):
		self.status_code = status_code
		self._payload = payload
		self.headers = headers or {}
		self.content = b"" if payload is None else json.dumps(payload).encode()
		self.text = "" if payload is None else json.dumps(payload)

	def json(self):
		if self._payload is None:
			raise ValueError("no body")
		return self._payload


class FakeSession:
	"""Replays queued responses in order and records every call made."""

	def __init__(self, responses: list[FakeResponse] | None = None):
		self.responses = deque(responses or [])
		self.calls: list[dict] = []

	def queue(self, *responses: FakeResponse) -> None:
		self.responses.extend(responses)

	def get(self, url, **kwargs):
		return self._next("GET", url, kwargs)

	def request(self, method, url, **kwargs):
		return self._next(method, url, kwargs)

	def _next(self, method, url, kwargs):
		self.calls.append({"method": method, "url": url, **kwargs})
		if not self.responses:
			raise AssertionError(f"Unexpected request: {method} {url}")
		return self.responses.popleft()

	def api_calls(self) -> list[dict]:
		return [call for call in self.calls if "/oauth-service/" not in call["url"]]


def token_response(token: str = "tok-1", expires_in: int = 3600) -> FakeResponse:
	return FakeResponse(200, {"access_token": token, "token_type": "bearer", "expires_in": expires_in})


def make_shipment(
	shipment_id: str = "SHIP-1",
	order_id: str = "OD1000001",
	items: list[dict] | None = None,
	location_id: str = "LOC-1",
) -> dict:
	"""A shipment shaped like GET /v3/shipments/{shipmentIds}."""
	return {
		"shipmentId": shipment_id,
		"locationId": location_id,
		"hold": False,
		"dispatchByDate": "2026-10-15T18:00:00+05:30",
		"orderItems": items
		if items is not None
		else [
			make_order_item(order_id=order_id, order_item_id="OI-1", sku="SKU-A", fsn="FSNAAAAAAAAAAAAA"),
		],
		"buyerDetails": {"firstName": "Asha", "lastName": "Rao"},
		"deliveryAddress": {
			"firstName": "Asha",
			"lastName": "Rao",
			"addressLine1": "12 MG Road",
			"addressLine2": "Indiranagar",
			"landmark": "Near Metro",
			"city": "Bengaluru",
			"stateName": "Karnataka",
			"state": "KA",
			"pincode": "560038",
			"contactNumber": "9876543210",
		},
	}


def make_order_item(
	order_id: str = "OD1000001",
	order_item_id: str = "OI-1",
	sku: str = "SKU-A",
	fsn: str = "FSNAAAAAAAAAAAAA",
	quantity: int = 1,
	customer_price: float = 499.0,
	shipping: float = 40.0,
	status: str = "APPROVED",
	is_replacement: bool = False,
) -> dict:
	return {
		"orderId": order_id,
		"orderItemId": order_item_id,
		"sku": sku,
		"fsn": fsn,
		"listingId": "LST1",
		"quantity": quantity,
		"status": status,
		"isReplacement": is_replacement,
		"orderDate": "2026-10-13T09:30:00+05:30",
		"priceComponents": {
			"sellingPrice": customer_price + 100,
			"customerPrice": customer_price,
			"shippingCharge": shipping,
			"totalPrice": customer_price + shipping,
			"flipkartDiscount": 0,
		},
	}


class FakeFlipkart:
	"""Stands in for FlipkartClient in site tests. Holds what "Flipkart" currently contains."""

	def __init__(self):
		self.open_shipments: list[dict] = []
		self.cancelled_shipments: list[dict] = []
		self.listings: list[dict] = []
		self.inventory_calls: list[dict] = []
		self.inventory_ok = True

	def iter_shipments(self, shipment_filter, *, page_size=20, sort=None):
		if shipment_filter["type"] == "cancelled":
			cancellation_type = shipment_filter["cancellationType"]
			yield from (s for s in self.cancelled_shipments if s["_cancellation_type"] == cancellation_type)
		else:
			yield from ({"shipmentId": s["shipmentId"]} for s in self.open_shipments)

	def get_shipments(self, shipment_ids):
		return [s for s in self.open_shipments if s["shipmentId"] in shipment_ids]

	def iter_listings(self, internal_state="ACTIVE", search_text=None):
		yield from (
			listing
			for listing in self.listings
			if listing.get("_state", "ACTIVE") == internal_state
			and (not search_text or search_text in listing["sku"])
		)

	def update_inventory(self, payload):
		from flipkart_integration.flipkart.api_client import InventoryResult

		self.inventory_calls.append(payload)
		return {
			sku: InventoryResult(self.inventory_ok, "" if self.inventory_ok else "rejected")
			for sku in payload
		}
