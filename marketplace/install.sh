#!/usr/bin/env bash
# Installs the marketplace, shipping and accounting apps bundled with this ERPNext fork on a site:
#
#   ecommerce_integrations  Shopify, Amazon, Unicommerce            third_party/ (git submodule)
#   bwh_shipping            Shiprocket, AfterShip labels            third_party/ (git submodule)
#   marketplace_guard       access control for tally_erpnext        marketplace_guard/
#   tally_erpnext           Tally Prime / Tally.ERP 9 sync          third_party/ (git submodule)
#   flipkart_integration    Flipkart orders and stock               flipkart_integration/
#
# Usage: marketplace/install.sh <bench-path> <site-name>
#
# The bench must already have frappe and this repository installed as the erpnext app, for example:
#   bench init --frappe-branch develop frappe-bench && cd frappe-bench
#   bench get-app erpnext https://github.com/namitjain07/erpnext --branch <your-branch>
#   bench new-site <site-name> --install-app erpnext
#
# The third-party apps are git submodules pinned to the commits this repository was tested with, so
# clone with `git clone --recurse-submodules`. This script fetches them if you forgot.
#
# Set SKIP_APPS="tally_erpnext bwh_shipping" (space separated) to leave apps out.

set -euo pipefail

# Order matters: dependencies first, and the guard before the app it protects.
APPS=(
	"ecommerce_integrations:third_party/ecommerce_integrations"
	"bwh_shipping:third_party/bwh_shipping"
	"flipkart_integration:flipkart_integration"
	"marketplace_guard:marketplace_guard"
	"tally_erpnext:third_party/tally_erpnext"
)

if [ "$#" -ne 2 ]; then
	echo "Usage: $0 <bench-path> <site-name>" >&2
	exit 1
fi

BENCH_PATH="$(cd "$1" && pwd)"
SITE="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_APPS="${SKIP_APPS:-}"

fail() {
	echo "error: $*" >&2
	exit 1
}

skipped() {
	[[ " $SKIP_APPS " == *" $1 "* ]]
}

[ -d "$BENCH_PATH/apps/frappe" ] || fail "$BENCH_PATH is not a bench (no apps/frappe)."
[ -d "$BENCH_PATH/apps/erpnext" ] || fail "erpnext is not in the bench. Run: bench get-app erpnext <this repository>."
[ -d "$BENCH_PATH/sites/$SITE" ] || fail "site $SITE does not exist in $BENCH_PATH/sites."

# The bench's erpnext must be this repository, otherwise the apps are installed next to a different ERPNext.
BENCH_ERPNEXT="$(cd "$BENCH_PATH/apps/erpnext" && pwd -P)"
if [ "$BENCH_ERPNEXT" != "$(cd "$REPO_ROOT" && pwd -P)" ]; then
	echo "warning: apps/erpnext in the bench is not this checkout ($BENCH_ERPNEXT)." >&2
	echo "         The apps are linked from $REPO_ROOT regardless." >&2
fi

if [ -f "$REPO_ROOT/.gitmodules" ] && [ -e "$REPO_ROOT/.git" ]; then
	echo "==> fetching git submodules"
	git -C "$REPO_ROOT" submodule update --init
fi

cd "$BENCH_PATH"

for entry in "${APPS[@]}"; do
	app="${entry%%:*}"
	source_dir="$REPO_ROOT/${entry#*:}"

	if skipped "$app"; then
		echo "==> $app: skipped"
		continue
	fi
	[ -f "$source_dir/pyproject.toml" ] || fail "$source_dir is empty. Clone with --recurse-submodules or run: git submodule update --init"

	if [ -d "apps/$app" ] && [ ! -L "apps/$app" ]; then
		fail "apps/$app is a regular directory (from bench get-app?). Remove it so it can be linked to $source_dir."
	fi

	echo "==> $app: linking and installing"
	# Frappe wants every app at apps/<app_name>/<app_name>, so apps that live inside this repository are linked in.
	ln -sfn "$source_dir" "apps/$app"
	if command -v uv >/dev/null 2>&1; then
		uv pip install --python env/bin/python -e "apps/$app"
	else
		env/bin/python -m pip install -e "apps/$app"
	fi
	grep -qx "$app" sites/apps.txt || echo "$app" >> sites/apps.txt
done

# Apps and modules are cached; without this the new apps are not picked up by install-app.
bench --site "$SITE" clear-cache

for entry in "${APPS[@]}"; do
	app="${entry%%:*}"
	skipped "$app" && continue
	echo "==> $app: installing on $SITE"
	bench --site "$SITE" install-app "$app"
done

bench --site "$SITE" migrate
bench --site "$SITE" clear-cache

cat <<DONE

Done. Restart the bench (or supervisor) so the workers load the new apps, then configure each one:
  Shopify, Amazon, Unicommerce   Ecommerce Integrations module
  Shiprocket, AfterShip          Shipping Provider Profile (book and print labels from a Delivery Note)
  Tally                          Tally Settings (off until you enable it; limited to Accounts Manager and System Manager)
  Flipkart                       Flipkart Settings, see flipkart_integration/README.md
DONE
