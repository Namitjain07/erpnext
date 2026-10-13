# Marketplace connectors for this ERPNext fork

ERPNext dropped its built-in Shopify and Amazon integrations in v14 and moved them to a separate app. This folder adds them back to this fork, plus a Flipkart connector that did not exist anywhere.

| Channel | App | Comes from | Orders in | Stock out |
| --- | --- | --- | --- | --- |
| Shopify | `ecommerce_integrations` | [frappe/ecommerce_integrations](https://github.com/frappe/ecommerce_integrations) | yes, by webhook | yes, per location |
| Amazon (SP-API) | `ecommerce_integrations` | same | yes, hourly, with products | **no** |
| Unicommerce (warehouse and multi-channel management) | `ecommerce_integrations` | same | yes, with returns and invoices | yes, per facility |
| Flipkart | `flipkart_integration` | [`flipkart_integration/`](../flipkart_integration/README.md) in this repository | yes, every few minutes | yes, per location |

The two apps are separate from ERPNext on purpose: `ecommerce_integrations` is maintained upstream and stays updatable, and nothing in `erpnext/` changed, so this fork still merges cleanly with `frappe/erpnext`.

Amazon stock is not pushed by the upstream connector. If you sell on Amazon, either manage stock for it on Amazon, or route it through Unicommerce.

## Requirements

Whatever this ERPNext version needs: Python 3.14, Node 24, MariaDB and Redis, and [bench](https://github.com/frappe/bench).

## Install

On a bench that already runs this fork as `erpnext`:

```bash
bench init --frappe-branch develop frappe-bench && cd frappe-bench
bench get-app erpnext https://github.com/namitjain07/erpnext --branch <your-branch>
bench new-site <site-name> --install-app erpnext

# from a clone of this repository
marketplace/install.sh /path/to/frappe-bench <site-name>
```

The script:

1. downloads `ecommerce_integrations` (branch `develop`; override with `ECOMMERCE_INTEGRATIONS_BRANCH`),
2. links `flipkart_integration/` into the bench's `apps/` folder and installs it,
3. installs both on the site and runs `bench migrate`.

Restart the bench afterwards so the workers pick up the new scheduler jobs. On Frappe Cloud or Docker, install `ecommerce_integrations` the usual way and add `flipkart_integration` as its own app by moving that folder to a repository of its own (`git subtree split --prefix=flipkart_integration`).

## Configure each channel

- **Shopify, Amazon, Unicommerce**: open the settings doctype of each and follow the [integration documentation](https://docs.frappe.io/erpnext/erpnext_integration). Amazon needs SP-API seller credentials, Shopify needs a custom app created in your Shopify admin.
- **Flipkart**: open **Flipkart Settings** and follow [`flipkart_integration/README.md`](../flipkart_integration/README.md).

## Running several channels on the same stock

ERPNext is the source of truth. Each channel gets a warehouse (or several) from ERPNext, mapped in its own settings. Two things keep the numbers honest:

- Every channel's orders must reach ERPNext as submitted Sales Orders, so that reserved stock is subtracted from what the other channels are offered.
- Give each channel its own warehouse when you can. A warehouse that two channels draw from can be oversold in the minutes between an order being placed and the next sync.

## Testing the setup

Without real seller accounts there is nothing to connect to, so nothing here has been run against Shopify, Amazon or Flipkart themselves. The Flipkart app has automated tests against a fake Flipkart; see its README. Use a Shopify development store and Flipkart's sandbox credentials (request them from Flipkart) before pointing a production shop at the site.
