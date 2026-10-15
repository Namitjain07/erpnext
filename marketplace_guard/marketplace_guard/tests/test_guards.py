import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests import IntegrationTestCase
from frappe.utils.password import update_password
from werkzeug.test import Client
from werkzeug.wrappers import Response

from marketplace_guard.guards import restrict_tally_api

BASIC_USER = "guard-basic@example.com"
ACCOUNTS_USER = "guard-accounts@example.com"
SITE_USERS = {BASIC_USER: [], ACCOUNTS_USER: ["Accounts Manager"]}

TALLY_PATHS = (
	"/api/method/tally_erpnext.tally.api.create_ledger",
	"/api/v2/method/tally_erpnext.tally.api.get_companies",
	"/api/method/tally_erpnext.tally.api.push_sales_invoice_to_tally",
)


def make_user(email: str, roles: list[str]) -> None:
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"enabled": 1,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)


class TestTallyGuard(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, roles in SITE_USERS.items():
			make_user(email, roles)

	def tearDown(self):
		frappe.set_user("Administrator")

	def call_as(self, user: str, path: str = "", cmd: str | None = None):
		frappe.set_user(user)
		form_dict = frappe._dict({"cmd": cmd} if cmd else {})
		with (
			patch.object(frappe.local, "request", SimpleNamespace(path=path), create=True),
			patch.object(frappe.local, "form_dict", form_dict),
		):
			return restrict_tally_api()

	def test_a_user_without_the_roles_is_refused_on_every_route(self):
		for path in TALLY_PATHS:
			with self.subTest(path=path), self.assertRaises(frappe.PermissionError):
				self.call_as(BASIC_USER, path)

	def test_the_legacy_cmd_parameter_is_covered(self):
		with self.assertRaises(frappe.PermissionError):
			self.call_as(BASIC_USER, "/", cmd="tally_erpnext.tally.api.create_ledger")

	def test_guest_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.call_as("Guest", TALLY_PATHS[0])

	def test_accounts_manager_and_administrator_are_allowed(self):
		for user in (ACCOUNTS_USER, "Administrator"):
			with self.subTest(user=user):
				self.assertIsNone(self.call_as(user, TALLY_PATHS[0]))
				self.assertIsNone(self.call_as(user, "/", cmd="tally_erpnext.tally.api.get_companies"))

	def test_other_endpoints_are_not_touched(self):
		for path in (
			"/api/method/ping",
			"/api/method/frappe.client.get_list",
			"/api/v2/method/flipkart_integration.flipkart.order.sync_new_orders",
			"/api/method/tally_erpnext_unrelated.api.something",
		):
			with self.subTest(path=path):
				self.assertIsNone(self.call_as(BASIC_USER, path))
				self.assertIsNone(self.call_as("Guest", path))


@unittest.skipUnless("tally_erpnext" in frappe.get_installed_apps(), "tally_erpnext is not installed")
class TestTallyGuardOverHttp(IntegrationTestCase):
	"""The same rule through the real request pipeline, so the hook is proven to be wired up, for both
	ways a user is identified: an API key and a login session."""

	PASSWORD = "Guard-Test-Pass-123!"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.keys = {}
		for email, roles in SITE_USERS.items():
			make_user(email, roles)
			update_password(email, cls.PASSWORD)
			user = frappe.get_doc("User", email)
			secret = frappe.generate_hash(length=20)
			user.api_key = user.api_key or frappe.generate_hash(length=15)
			user.api_secret = secret
			user.save(ignore_permissions=True)
			cls.keys[email] = f"token {user.api_key}:{secret}"
		frappe.db.commit()

	def call_over_http(self, path: str, user: str | None = None, login: bool = False) -> int:
		"""Status code of POSTing to `path` as `user` (API key, or a login session when `login`) or as a guest."""
		site = frappe.local.site
		authorization = self.keys.get(user) if user and not login else None
		result = {}

		def run():
			client = Client(application, Response, use_cookies=True)
			headers = {"X-Frappe-Site-Name": site}
			if login:
				client.post("/api/method/login", headers=headers, json={"usr": user, "pwd": self.PASSWORD})
			if authorization:
				headers["Authorization"] = authorization
			result["status"] = client.post(path, headers=headers, json={}).status_code

		worker = threading.Thread(target=run)
		worker.start()
		worker.join()
		return result["status"]

	def test_api_key_users(self):
		for path in TALLY_PATHS[:2]:
			with self.subTest(path=path):
				self.assertEqual(self.call_over_http(path, BASIC_USER), 403)
				self.assertNotEqual(self.call_over_http(path, ACCOUNTS_USER), 403)

	def test_session_users(self):
		for path in TALLY_PATHS[:2]:
			with self.subTest(path=path):
				self.assertEqual(self.call_over_http(path, BASIC_USER, login=True), 403)
				self.assertNotEqual(self.call_over_http(path, ACCOUNTS_USER, login=True), 403)

	def test_anonymous_requests_are_refused(self):
		self.assertEqual(self.call_over_http(TALLY_PATHS[0]), 403)

	def test_other_endpoints_still_work_for_everyone(self):
		self.assertEqual(self.call_over_http("/api/method/ping", BASIC_USER), 200)
		self.assertEqual(self.call_over_http("/api/method/ping"), 200)
