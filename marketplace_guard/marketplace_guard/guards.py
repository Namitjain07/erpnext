import re

import frappe

TALLY_API_ROLES = ("System Manager", "Accounts Manager")
TALLY_API_PATH = re.compile(r"^/api/(?:v\d+/)?method/tally_erpnext\.")
TALLY_API_PREFIX = "tally_erpnext."


def restrict_tally_api() -> None:
	"""tally_erpnext exposes its API as plain whitelisted functions that any logged-in user may call,
	and they create Tally ledgers and vouchers and push any Sales Invoice by name. This runs as an
	auth hook, which Frappe calls after it has resolved the user from a session cookie, an API key or
	OAuth and before it dispatches the request. Every way of reaching those functions is covered
	without touching the app: REST v1, REST v2 and the legacy `cmd` parameter.

	Internal calls, such as the scheduler, never pass through here.
	"""
	request = getattr(frappe.local, "request", None)
	path = getattr(request, "path", "") or ""
	cmd = frappe.form_dict.get("cmd") or ""

	if TALLY_API_PATH.match(path) or str(cmd).startswith(TALLY_API_PREFIX):
		frappe.only_for(TALLY_API_ROLES)
