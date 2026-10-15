"""Client for the Flipkart Marketplace Seller API.

Kept free of Frappe imports so it can be tested without a site. Endpoints and payloads follow
https://seller.flipkart.com/api-docs/ (Order Management v3 and Listing Management v3).
"""

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_BASE_URL = "https://api.flipkart.net"
TOKEN_PATH = "/oauth-service/oauth/token"
TOKEN_SCOPE = "Seller_Api"
SHIPMENT_FILTER_PATH = "/sellers/v3/shipments/filter/"
SHIPMENTS_PATH = "/sellers/v3/shipments"
LISTING_SEARCH_PATH = "/sellers/listings/v3/product/search"
INVENTORY_UPDATE_PATH = "/sellers/listings/v3/update/inventory"

SHIPMENT_PAGE_SIZE = 20
SHIPMENT_FETCH_BATCH = 25
LISTING_PAGE_SIZE = 20
INVENTORY_UPDATE_BATCH = 10

RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TOKEN_EXPIRY_MARGIN_SECONDS = 120
DEFAULT_TOKEN_TTL_SECONDS = 3600
MAX_BACKOFF_SECONDS = 30
INVENTORY_FAILURE_STATUSES = frozenset({"FAILURE", "FAILED", "ERROR"})


class FlipkartAPIError(Exception):
	def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
		super().__init__(message)
		self.message = message
		self.status_code = status_code
		self.payload = payload


@dataclass
class InventoryResult:
	ok: bool
	message: str = ""


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
	for start in range(0, len(items), size):
		yield items[start : start + size]


class FlipkartClient:
	def __init__(
		self,
		app_id: str,
		app_secret: str,
		base_url: str = DEFAULT_BASE_URL,
		*,
		session: requests.Session | None = None,
		token: str | None = None,
		token_expires_at: float | None = None,
		on_token: Callable[[str, float], None] | None = None,
		timeout: float = 30,
		max_retries: int = 3,
		backoff: float = 1.0,
		sleep: Callable[[float], None] = time.sleep,
		clock: Callable[[], float] = time.time,
	):
		self.app_id = app_id
		self.app_secret = app_secret
		self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
		self.timeout = timeout
		self.max_retries = max_retries
		self.backoff = backoff
		self._session = session or requests.Session()
		self._token = token
		self._token_expires_at = token_expires_at
		self._on_token = on_token
		self._sleep = sleep
		self._clock = clock

	def authenticate(self, force: bool = False) -> str:
		now = self._clock()
		if (
			not force
			and self._token
			and self._token_expires_at
			and now < self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS
		):
			return self._token

		try:
			response = self._session.get(
				self._url(TOKEN_PATH),
				params={"grant_type": "client_credentials", "scope": TOKEN_SCOPE},
				auth=(self.app_id, self.app_secret),
				timeout=self.timeout,
			)
		except (requests.ConnectionError, requests.Timeout) as exc:
			raise FlipkartAPIError(f"Could not reach Flipkart to authenticate: {exc}") from exc

		payload = _json_or_none(response)
		token = payload.get("access_token") if isinstance(payload, dict) else None
		if response.status_code != 200 or not token:
			message = f"Flipkart rejected the application credentials (HTTP {response.status_code})"
			if isinstance(payload, dict) and (reason := _error_text(payload, response).strip()):
				message = f"{message}: {reason}"
			raise FlipkartAPIError(message, status_code=response.status_code, payload=payload)

		expires_in = float(payload.get("expires_in") or DEFAULT_TOKEN_TTL_SECONDS)
		self._token = token
		self._token_expires_at = now + expires_in
		if self._on_token:
			self._on_token(token, self._token_expires_at)
		return token

	def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
		url = self._url(path)
		attempt = 0
		reauthenticated = False

		while True:
			headers = {"Authorization": f"Bearer {self.authenticate()}", "Accept": "application/json"}
			if json is not None:
				headers["Content-Type"] = "application/json; charset=utf-8"

			try:
				response = self._session.request(
					method, url, params=params, json=json, headers=headers, timeout=self.timeout
				)
			except (requests.ConnectionError, requests.Timeout) as exc:
				if attempt >= self.max_retries:
					raise FlipkartAPIError(f"Could not reach Flipkart: {exc}") from exc
				self._wait(attempt)
				attempt += 1
				continue

			if response.status_code == 401 and not reauthenticated:
				reauthenticated = True
				self._token = None
				continue

			if response.status_code in RETRY_STATUS_CODES and attempt < self.max_retries:
				self._wait(attempt, response.headers.get("Retry-After"))
				attempt += 1
				continue

			if response.status_code >= 400:
				payload = _json_or_none(response)
				raise FlipkartAPIError(
					f"Flipkart API {method} {path} failed with HTTP {response.status_code}: "
					f"{_error_text(payload, response)}",
					status_code=response.status_code,
					payload=payload,
				)

			return _json_or_none(response) if response.content else {}

	def iter_shipments(
		self, shipment_filter: dict, *, page_size: int = SHIPMENT_PAGE_SIZE, sort: dict | None = None
	) -> Iterator[dict]:
		body: dict[str, Any] = {"filter": shipment_filter, "pagination": {"pageSize": page_size}}
		if sort:
			body["sort"] = sort

		page = self.request("POST", SHIPMENT_FILTER_PATH, json=body)
		while True:
			yield from page.get("shipments") or []

			next_url = page.get("nextPageUrl")
			if not (page.get("hasMore") and next_url):
				return
			page = self.request("GET", self._next_page_path(next_url))

	def get_shipments(self, shipment_ids: Sequence[str]) -> list[dict]:
		"""Full shipment details, including delivery address and buyer name."""
		shipments: list[dict] = []
		for batch in chunked(list(shipment_ids), SHIPMENT_FETCH_BATCH):
			ids = ",".join(quote(str(shipment_id), safe="") for shipment_id in batch)
			data = self.request("GET", f"{SHIPMENTS_PATH}/{ids}")
			shipments.extend(data.get("shipments") or [] if isinstance(data, dict) else data or [])
		return shipments

	def iter_listings(self, internal_state: str = "ACTIVE", search_text: str | None = None) -> Iterator[dict]:
		batch_no = 0
		while True:
			body: dict[str, Any] = {"batchNo": batch_no, "internalState": internal_state}
			if search_text:
				body["searchText"] = search_text

			data = self.request("POST", LISTING_SEARCH_PATH, json=body)
			rows = data.get("listingData") or []
			yield from rows

			batch_no += 1
			total = data.get("totalListingsCount")
			if len(rows) < LISTING_PAGE_SIZE or (total is not None and batch_no * LISTING_PAGE_SIZE >= total):
				return

	def update_inventory(self, payload: dict[str, dict]) -> dict[str, InventoryResult]:
		"""Push absolute stock levels. `payload` maps seller SKU to
		{"product_id": ..., "locations": [{"id": ..., "inventory": ...}]}.

		Flipkart answers HTTP 200 even when individual SKUs fail, so every SKU gets its own result.
		"""
		results: dict[str, InventoryResult] = {}
		for batch in chunked(list(payload.items()), INVENTORY_UPDATE_BATCH):
			response = self.request("POST", INVENTORY_UPDATE_PATH, json=dict(batch))
			for sku, _ in batch:
				entry = response.get(sku) if isinstance(response, dict) else None
				results[sku] = parse_inventory_result(entry)
		return results

	def check_connection(self) -> None:
		self.authenticate(force=True)
		self.request("POST", LISTING_SEARCH_PATH, json={"batchNo": 0, "internalState": "ACTIVE"})

	def _url(self, path: str) -> str:
		if path.startswith(("http://", "https://")):
			return path
		return self.base_url + (path if path.startswith("/") else "/" + path)

	def _next_page_path(self, next_url: str) -> str:
		if next_url.startswith(("http://", "https://")):
			if not next_url.startswith(self.base_url + "/"):
				raise FlipkartAPIError(
					f"Refusing to follow a next page URL outside {self.base_url}: {next_url}"
				)
			return next_url
		if next_url.startswith("/sellers/"):
			return next_url
		return "/sellers" + (next_url if next_url.startswith("/") else "/" + next_url)

	def _wait(self, attempt: int, retry_after: str | None = None) -> None:
		try:
			delay = float(retry_after) if retry_after else self.backoff * 2**attempt
		except ValueError:
			delay = self.backoff * 2**attempt
		self._sleep(min(delay, MAX_BACKOFF_SECONDS))


def parse_inventory_result(entry: Any) -> InventoryResult:
	if not isinstance(entry, dict):
		return InventoryResult(False, "Flipkart returned no result for this SKU")

	problems = [
		error
		for error in (entry.get("errors") or []) + (entry.get("attribute_errors") or [])
		if str(error.get("severity") or "ERROR").upper() != "WARNING"
	]
	status = str(entry.get("status") or "").upper()

	if problems or status in INVENTORY_FAILURE_STATUSES:
		messages = [str(error.get("description") or error.get("code") or "") for error in problems]
		return InventoryResult(False, "; ".join(m for m in messages if m) or status or "Failed")
	return InventoryResult(True)


def _json_or_none(response: requests.Response) -> Any:
	try:
		return response.json()
	except ValueError:
		return None


def _error_text(payload: Any, response: requests.Response) -> str:
	if isinstance(payload, dict):
		for key in ("message", "error_description", "error", "description"):
			if payload.get(key):
				return str(payload[key])
	return (response.text or "")[:300]
