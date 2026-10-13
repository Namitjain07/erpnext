import unittest

from flipkart_integration.flipkart.api_client import (
	DEFAULT_BASE_URL,
	FlipkartAPIError,
	FlipkartClient,
	parse_inventory_result,
)
from flipkart_integration.tests.fakes import FakeResponse, FakeSession, token_response


class Clock:
	def __init__(self, now: float = 1_000_000.0):
		self.now = now

	def __call__(self) -> float:
		return self.now


def make_client(session: FakeSession, **kwargs) -> FlipkartClient:
	sleeps: list[float] = []
	client = FlipkartClient(
		"app-id",
		"app-secret",
		session=session,
		sleep=sleeps.append,
		clock=kwargs.pop("clock", Clock()),
		**kwargs,
	)
	client.sleeps = sleeps
	return client


class TestAuthentication(unittest.TestCase):
	def test_token_request_uses_basic_auth_and_client_credentials(self):
		session = FakeSession([token_response()])
		client = make_client(session)

		self.assertEqual(client.authenticate(), "tok-1")

		call = session.calls[0]
		self.assertEqual(call["url"], f"{DEFAULT_BASE_URL}/oauth-service/oauth/token")
		self.assertEqual(call["params"], {"grant_type": "client_credentials", "scope": "Seller_Api"})
		self.assertEqual(call["auth"], ("app-id", "app-secret"))

	def test_token_is_reused_until_close_to_expiry(self):
		clock = Clock()
		session = FakeSession([token_response("tok-1", expires_in=1000), token_response("tok-2")])
		client = make_client(session, clock=clock)

		client.authenticate()
		clock.now += 500
		self.assertEqual(client.authenticate(), "tok-1")
		self.assertEqual(len(session.calls), 1)

		clock.now += 450
		self.assertEqual(client.authenticate(), "tok-2")

	def test_on_token_callback_receives_token_and_expiry(self):
		seen = []
		session = FakeSession([token_response("tok-1", expires_in=100)])
		client = make_client(session, on_token=lambda token, expires_at: seen.append((token, expires_at)))

		client.authenticate()

		self.assertEqual(seen, [("tok-1", 1_000_100.0)])

	def test_cached_token_skips_the_token_endpoint(self):
		session = FakeSession([FakeResponse(200, {"ok": True})])
		client = make_client(session, token="cached", token_expires_at=1_000_000.0 + 5000)

		client.request("GET", "/sellers/anything")

		self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer cached")
		self.assertEqual(len(session.calls), 1)

	def test_rejected_credentials_raise(self):
		session = FakeSession([FakeResponse(401, {"error": "invalid_client"})])
		client = make_client(session)

		with self.assertRaises(FlipkartAPIError) as ctx:
			client.authenticate()

		self.assertEqual(ctx.exception.status_code, 401)


class TestRequestHandling(unittest.TestCase):
	def test_unauthorized_response_refreshes_token_once(self):
		session = FakeSession(
			[
				token_response("old"),
				FakeResponse(401, {"message": "expired"}),
				token_response("new"),
				FakeResponse(200, {"done": True}),
			]
		)
		client = make_client(session)

		self.assertEqual(client.request("GET", "/sellers/x"), {"done": True})
		self.assertEqual(session.api_calls()[1]["headers"]["Authorization"], "Bearer new")

	def test_persistent_unauthorized_response_raises(self):
		session = FakeSession(
			[
				token_response("a"),
				FakeResponse(401, {"message": "no"}),
				token_response("b"),
				FakeResponse(401, {"message": "still no"}),
			]
		)
		client = make_client(session)

		with self.assertRaises(FlipkartAPIError) as ctx:
			client.request("GET", "/sellers/x")

		self.assertEqual(ctx.exception.status_code, 401)

	def test_rate_limit_is_retried_honouring_retry_after(self):
		session = FakeSession(
			[
				token_response(),
				FakeResponse(429, {"message": "slow down"}, headers={"Retry-After": "7"}),
				FakeResponse(200, {"ok": 1}),
			]
		)
		client = make_client(session)

		self.assertEqual(client.request("GET", "/sellers/x"), {"ok": 1})
		self.assertEqual(client.sleeps, [7.0])

	def test_server_errors_back_off_exponentially_then_give_up(self):
		session = FakeSession([token_response()] + [FakeResponse(503, {"message": "down"})] * 4)
		client = make_client(session, max_retries=3)

		with self.assertRaises(FlipkartAPIError) as ctx:
			client.request("GET", "/sellers/x")

		self.assertEqual(ctx.exception.status_code, 503)
		self.assertEqual(client.sleeps, [1.0, 2.0, 4.0])

	def test_client_errors_are_not_retried(self):
		session = FakeSession([token_response(), FakeResponse(400, {"message": "bad filter"})])
		client = make_client(session)

		with self.assertRaises(FlipkartAPIError) as ctx:
			client.request("POST", "/sellers/x", json={})

		self.assertIn("bad filter", ctx.exception.message)
		self.assertEqual(client.sleeps, [])

	def test_json_body_is_sent_with_utf8_content_type(self):
		session = FakeSession([token_response(), FakeResponse(200, {})])
		client = make_client(session)

		client.request("POST", "/sellers/x", json={"a": 1})

		call = session.api_calls()[0]
		self.assertEqual(call["json"], {"a": 1})
		self.assertEqual(call["headers"]["Content-Type"], "application/json; charset=utf-8")


class TestShipments(unittest.TestCase):
	def test_filter_follows_next_page_urls(self):
		session = FakeSession(
			[
				token_response(),
				FakeResponse(
					200,
					{
						"hasMore": True,
						"nextPageUrl": "/v3/shipments/filter?page=2",
						"shipments": [{"shipmentId": "S1"}],
					},
				),
				FakeResponse(
					200,
					{
						"hasMore": True,
						"nextPageUrl": "/sellers/v3/shipments/filter?page=3",
						"shipments": [{"shipmentId": "S2"}],
					},
				),
				FakeResponse(200, {"hasMore": False, "shipments": [{"shipmentId": "S3"}]}),
			]
		)
		client = make_client(session)

		shipments = list(
			client.iter_shipments(
				{"type": "preDispatch", "states": ["APPROVED"]}, sort={"field": "orderDate"}
			)
		)

		self.assertEqual([s["shipmentId"] for s in shipments], ["S1", "S2", "S3"])
		calls = session.api_calls()
		self.assertEqual(calls[0]["method"], "POST")
		self.assertEqual(calls[0]["url"], f"{DEFAULT_BASE_URL}/sellers/v3/shipments/filter/")
		self.assertEqual(
			calls[0]["json"],
			{
				"filter": {"type": "preDispatch", "states": ["APPROVED"]},
				"pagination": {"pageSize": 20},
				"sort": {"field": "orderDate"},
			},
		)
		self.assertEqual(calls[1]["method"], "GET")
		self.assertEqual(calls[1]["url"], f"{DEFAULT_BASE_URL}/sellers/v3/shipments/filter?page=2")
		self.assertEqual(calls[2]["url"], f"{DEFAULT_BASE_URL}/sellers/v3/shipments/filter?page=3")

	def test_next_page_url_on_the_same_host_is_followed(self):
		session = FakeSession(
			[
				token_response(),
				FakeResponse(
					200,
					{
						"hasMore": True,
						"nextPageUrl": f"{DEFAULT_BASE_URL}/sellers/v3/shipments/filter?page=2",
						"shipments": [{"shipmentId": "S1"}],
					},
				),
				FakeResponse(200, {"hasMore": False, "shipments": [{"shipmentId": "S2"}]}),
			]
		)
		client = make_client(session)

		self.assertEqual(len(list(client.iter_shipments({"type": "preDispatch", "states": []}))), 2)

	def test_next_page_url_on_another_host_is_refused_before_sending_the_token(self):
		session = FakeSession(
			[
				token_response(),
				FakeResponse(
					200,
					{
						"hasMore": True,
						"nextPageUrl": "https://evil.example.com/sellers/v3/shipments/filter?page=2",
						"shipments": [{"shipmentId": "S1"}],
					},
				),
			]
		)
		client = make_client(session)

		with self.assertRaises(FlipkartAPIError):
			list(client.iter_shipments({"type": "preDispatch", "states": []}))

		self.assertEqual(len(session.api_calls()), 1)

	def test_filter_stops_when_has_more_is_false(self):
		session = FakeSession(
			[token_response(), FakeResponse(200, {"hasMore": False, "nextPageUrl": "/x", "shipments": []})]
		)
		client = make_client(session)

		self.assertEqual(list(client.iter_shipments({"type": "preDispatch", "states": []})), [])
		self.assertEqual(len(session.api_calls()), 1)

	def test_details_are_fetched_in_batches_of_25(self):
		ids = [f"S{i}" for i in range(30)]
		session = FakeSession(
			[
				token_response(),
				FakeResponse(200, {"shipments": [{"shipmentId": i} for i in ids[:25]]}),
				FakeResponse(200, {"shipments": [{"shipmentId": i} for i in ids[25:]]}),
			]
		)
		client = make_client(session)

		result = client.get_shipments(ids)

		self.assertEqual(len(result), 30)
		calls = session.api_calls()
		self.assertEqual(len(calls), 2)
		self.assertEqual(calls[0]["url"].rsplit("/", 1)[1].count(","), 24)
		self.assertEqual(calls[1]["url"].rsplit("/", 1)[1], "S25,S26,S27,S28,S29")

	def test_shipment_ids_are_url_quoted(self):
		session = FakeSession([token_response(), FakeResponse(200, {"shipments": []})])
		client = make_client(session)

		client.get_shipments(["a/b"])

		self.assertTrue(session.api_calls()[0]["url"].endswith("/sellers/v3/shipments/a%2Fb"))


class TestListings(unittest.TestCase):
	def test_listings_are_paged_by_batch_number(self):
		full_page = [{"sku": f"S{i}"} for i in range(20)]
		session = FakeSession(
			[
				token_response(),
				FakeResponse(200, {"totalListingsCount": 25, "listingData": full_page}),
				FakeResponse(200, {"totalListingsCount": 25, "listingData": [{"sku": "S20"}]}),
			]
		)
		client = make_client(session)

		listings = list(client.iter_listings("ACTIVE", search_text="S"))

		self.assertEqual(len(listings), 21)
		calls = session.api_calls()
		self.assertEqual(calls[0]["json"], {"batchNo": 0, "internalState": "ACTIVE", "searchText": "S"})
		self.assertEqual(calls[1]["json"]["batchNo"], 1)

	def test_listing_paging_stops_at_reported_total(self):
		page = [{"sku": f"S{i}"} for i in range(20)]
		session = FakeSession(
			[token_response(), FakeResponse(200, {"totalListingsCount": 20, "listingData": page})]
		)
		client = make_client(session)

		self.assertEqual(len(list(client.iter_listings())), 20)
		self.assertEqual(len(session.api_calls()), 1)


class TestInventoryUpdate(unittest.TestCase):
	def test_skus_are_sent_in_batches_of_ten_with_per_sku_results(self):
		payload = {
			f"SKU-{i}": {"product_id": "FSN", "locations": [{"id": "L1", "inventory": i}]} for i in range(12)
		}
		first = {f"SKU-{i}": {"status": "SUCCESS"} for i in range(10)}
		first["SKU-3"] = {
			"status": "FAILURE",
			"errors": [{"severity": "ERROR", "code": 1, "description": "no such location"}],
		}
		second = {"SKU-10": {"status": "SUCCESS"}}
		session = FakeSession([token_response(), FakeResponse(200, first), FakeResponse(200, second)])
		client = make_client(session)

		results = client.update_inventory(payload)

		calls = session.api_calls()
		self.assertEqual([len(call["json"]) for call in calls], [10, 2])
		self.assertEqual(calls[0]["url"], f"{DEFAULT_BASE_URL}/sellers/listings/v3/update/inventory")
		self.assertTrue(results["SKU-0"].ok)
		self.assertFalse(results["SKU-3"].ok)
		self.assertEqual(results["SKU-3"].message, "no such location")
		self.assertFalse(results["SKU-11"].ok)

	def test_warnings_do_not_fail_a_sku(self):
		result = parse_inventory_result(
			{"status": "SUCCESS", "errors": [{"severity": "WARNING", "description": "price is stale"}]}
		)
		self.assertTrue(result.ok)

	def test_attribute_errors_fail_a_sku(self):
		result = parse_inventory_result(
			{"attribute_errors": [{"severity": "ERROR", "description": "bad", "path": "locations[0]"}]}
		)
		self.assertFalse(result.ok)

	def test_missing_entry_fails(self):
		self.assertFalse(parse_inventory_result(None).ok)
