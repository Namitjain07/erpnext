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
bench --site "$SITE" execute ecommerce_integrations.utils.before_test.before_tests
bench --site "$SITE" execute frappe.db.set_single_value --args '["Selling Settings", "allow_multiple_items", 1]'
for pair in "INR USD 0.012" "USD INR 83"; do
	set -- $pair
	bench --site "$SITE" execute frappe.client.insert --kwargs \
		"{\"doc\": {\"doctype\": \"Currency Exchange\", \"date\": \"2000-01-01\", \"from_currency\": \"$1\", \"to_currency\": \"$2\", \"exchange_rate\": $3, \"for_buying\": 1, \"for_selling\": 1}}"
done
bench --site "$SITE" execute frappe.tests.utils.make_test_records --args '["Unicommerce Channel"]' --kwargs '{"commit": True}'

echo "==> running the upstream suite"
bench --site "$SITE" run-tests --app ecommerce_integrations --skip-before-tests
