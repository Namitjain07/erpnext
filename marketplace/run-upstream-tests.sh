#!/usr/bin/env bash
# Runs the upstream ecommerce_integrations test suite (Shopify, Amazon, Unicommerce) against this fork,
# on a throwaway site that is dropped afterwards.
#
# Usage: MARIADB_ROOT_PASSWORD=... marketplace/run-upstream-tests.sh <bench-path>
#
# The suite was written for an older Frappe test framework and only passes on a database prepared the way
# CI prepares one, and only once per database: it commits customers, orders and invoices that a second run
# trips over. This script does the preparation, so use a fresh site every time rather than re-running on one:
#
#   1. the company "Wind Power LLC" must be the Indian one (INR) that upstream's own before_tests creates.
#      ERPNext's own test records define a US-dollar company with the same name, whichever is created first
#      wins, so the Indian one is created before anything else.
#   2. Selling Settings must allow the same item on more than one row, because Unicommerce sends one row per unit.
#   3. INR/USD exchange rates must exist, because ERPNext otherwise fetches them from the internet.
#   4. The "Unicommerce Channel" test records must be committed, because Frappe 17 rolls each test class back.
#
# Set KEEP_SITE=1 to keep the site for inspection.

set -euo pipefail

if [ "$#" -ne 1 ]; then
	echo "Usage: MARIADB_ROOT_PASSWORD=... $0 <bench-path>" >&2
	exit 1
fi
: "${MARIADB_ROOT_PASSWORD:?set MARIADB_ROOT_PASSWORD to the MariaDB root password}"

BENCH_PATH="$(cd "$1" && pwd)"
SITE="upstream-tests-$$.localhost"

[ -d "$BENCH_PATH/apps/ecommerce_integrations" ] || {
	echo "error: ecommerce_integrations is not in the bench; run marketplace/install.sh first." >&2
	exit 1
}

cd "$BENCH_PATH"

cleanup() {
	if [ "${KEEP_SITE:-0}" = "1" ]; then
		echo "==> keeping site $SITE"
	else
		echo "==> dropping site $SITE"
		bench drop-site "$SITE" --force --root-password "$MARIADB_ROOT_PASSWORD" >/dev/null 2>&1 || true
	fi
}
trap cleanup EXIT

echo "==> creating $SITE"
bench new-site "$SITE" --mariadb-root-password "$MARIADB_ROOT_PASSWORD" --admin-password admin \
	--install-app erpnext --install-app ecommerce_integrations >/dev/null
bench --site "$SITE" set-config allow_tests true

echo "==> preparing the database"
# Test mode matters: upstream's bootstrap creates a user with the password "test", which Frappe only accepts in tests
# (it reads frappe.in_test, which the test runner sets along with frappe.flags.in_test).
# Frappe finds its log folder relative to the working directory, which has to be sites/.
(cd "$BENCH_PATH/sites" && "$BENCH_PATH/env/bin/python" -I - . "$SITE" <<'PYTHON'
import sys

import frappe

sites_path, site = sys.argv[1:3]
frappe.init(site=site, sites_path=sites_path)
frappe.connect()
frappe.in_test = True
frappe.flags.in_test = True

from ecommerce_integrations.utils.before_test import before_tests
from frappe.tests.utils import make_test_records

before_tests()
frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 1)
for from_currency, to_currency, rate in (("INR", "USD", 0.012), ("USD", "INR", 83)):
	frappe.get_doc(
		{
			"doctype": "Currency Exchange",
			"date": "2000-01-01",
			"from_currency": from_currency,
			"to_currency": to_currency,
			"exchange_rate": rate,
			"for_buying": 1,
			"for_selling": 1,
		}
	).insert(ignore_permissions=True)
make_test_records("Unicommerce Channel", commit=True)
frappe.db.commit()
frappe.destroy()
PYTHON
)

echo "==> running the upstream suite"
bench --site "$SITE" run-tests --app ecommerce_integrations --skip-before-tests
