#!/usr/bin/env bash
# Adds the marketplace connectors to a bench that runs this ERPNext fork:
#   - ecommerce_integrations (Shopify, Amazon, Unicommerce)
#   - flipkart_integration   (Flipkart, from the flipkart_integration/ folder of this repository)
#
# Usage: marketplace/install.sh <bench-path> <site-name>
#
# The bench must already have frappe and this repository installed as the erpnext app, for example:
#   bench init --frappe-branch develop frappe-bench && cd frappe-bench
#   bench get-app erpnext https://github.com/namitjain07/erpnext --branch <your-branch>
#   bench new-site <site-name> --install-app erpnext

set -euo pipefail

ECOMMERCE_INTEGRATIONS_URL="${ECOMMERCE_INTEGRATIONS_URL:-https://github.com/frappe/ecommerce_integrations}"
ECOMMERCE_INTEGRATIONS_BRANCH="${ECOMMERCE_INTEGRATIONS_BRANCH:-develop}"

if [ "$#" -ne 2 ]; then
	echo "Usage: $0 <bench-path> <site-name>" >&2
	exit 1
fi

BENCH_PATH="$(cd "$1" && pwd)"
SITE="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLIPKART_SRC="$REPO_ROOT/flipkart_integration"

fail() {
	echo "error: $*" >&2
	exit 1
}

[ -d "$BENCH_PATH/apps/frappe" ] || fail "$BENCH_PATH is not a bench (no apps/frappe)."
[ -d "$BENCH_PATH/apps/erpnext" ] || fail "erpnext is not in the bench. Run: bench get-app erpnext <this repository>."
[ -f "$FLIPKART_SRC/pyproject.toml" ] || fail "$FLIPKART_SRC is missing; run this script from a full checkout of the repository."
[ -d "$BENCH_PATH/sites/$SITE" ] || fail "site $SITE does not exist in $BENCH_PATH/sites."

# The bench's erpnext must be this repository, otherwise the Flipkart app is installed next to a different ERPNext.
BENCH_ERPNEXT="$(cd "$BENCH_PATH/apps/erpnext" && pwd -P)"
if [ "$BENCH_ERPNEXT" != "$(cd "$REPO_ROOT" && pwd -P)" ]; then
	echo "warning: apps/erpnext in the bench is not this checkout ($BENCH_ERPNEXT)." >&2
	echo "         The Flipkart app is linked from $FLIPKART_SRC regardless." >&2
fi

cd "$BENCH_PATH"

echo "==> ecommerce_integrations (Shopify, Amazon, Unicommerce)"
if [ ! -d apps/ecommerce_integrations ]; then
	bench get-app ecommerce_integrations "$ECOMMERCE_INTEGRATIONS_URL" --branch "$ECOMMERCE_INTEGRATIONS_BRANCH"
else
	echo "already in the bench, skipping download"
fi

echo "==> flipkart_integration (Flipkart)"
# Frappe wants every app at apps/<app_name>/<app_name>, so the app that sits inside this repository is linked in.
ln -sfn "$FLIPKART_SRC" apps/flipkart_integration
if command -v uv >/dev/null 2>&1; then
	uv pip install --python env/bin/python -e apps/flipkart_integration
else
	env/bin/python -m pip install -e apps/flipkart_integration
fi
grep -qx flipkart_integration sites/apps.txt || echo flipkart_integration >> sites/apps.txt

# Apps and modules are cached; without this the new app is not picked up by install-app.
bench --site "$SITE" clear-cache

echo "==> installing on $SITE"
bench --site "$SITE" install-app ecommerce_integrations
bench --site "$SITE" install-app flipkart_integration
bench --site "$SITE" migrate
bench --site "$SITE" clear-cache

cat <<DONE

Done. Restart the bench (or supervisor) so the workers load the new apps, then configure each channel:
  Shopify, Amazon, Unicommerce   Ecommerce Integrations module, see the app's documentation
  Flipkart                       Flipkart Settings, see flipkart_integration/README.md
DONE
