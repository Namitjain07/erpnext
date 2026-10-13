# Marketplace, shipping and accounting apps for this ERPNext fork

ERPNext dropped its built-in Shopify and Amazon integrations in v14 and moved them to a separate app. This fork brings them back, together with shipping labels, Tally and a Flipkart connector that did not exist anywhere. Nothing in `erpnext/` changed, so the fork still merges cleanly with `frappe/erpnext`.

| App | What it gives you | Where it lives | Licence |
| --- | --- | --- | --- |
| [`ecommerce_integrations`](https://github.com/frappe/ecommerce_integrations) | Shopify, Amazon (SP-API), Unicommerce (multi-channel warehouse management) | `third_party/ecommerce_integrations` (submodule) | GPL-3.0 |
| [`bwh_shipping`](https://github.com/bwhtech/bwh_shipping) | Shiprocket and AfterShip: rates, booking, labels and tracking from a Delivery Note | `third_party/bwh_shipping` (submodule) | MIT |
| [`tally_erpnext`](https://github.com/svnix-solutions/tally_erpnext) | Sync with TallyPrime and Tally.ERP 9 | `third_party/tally_erpnext` (submodule) | GPL-3.0 |
| `marketplace_guard` | Restricts Tally's API to Accounts Managers and System Managers (see below) | [`marketplace_guard/`](../marketplace_guard/README.md) | GPL-3.0 |
| `flipkart_integration` | Flipkart orders in, stock out | [`flipkart_integration/`](../flipkart_integration/README.md) | GPL-3.0 |

## Install

Clone with the submodules, build a bench that runs this fork, and run one script:

```bash
git clone --recurse-submodules https://github.com/namitjain07/erpnext
bench init --frappe-branch develop frappe-bench && cd frappe-bench
bench get-app erpnext /path/to/the/clone        # or the GitHub URL and your branch
bench new-site <site-name> --install-app erpnext

/path/to/the/clone/marketplace/install.sh /path/to/frappe-bench <site-name>
```

The script links each app into the bench, installs it on the site and runs `bench migrate`. It also fetches the submodules if you cloned without them. Leave apps out with `SKIP_APPS="tally_erpnext bwh_shipping" marketplace/install.sh ...`. Restart the bench afterwards so the workers pick up the new scheduler jobs.

What it does not do is build the bench itself, because that depends on your machine: Python 3.14, Node 24, MariaDB, Redis and [bench](https://github.com/frappe/bench).

The three third-party apps are **pinned to the commits this repository was tested with**, so an upstream change cannot reach your accounting or shipping setup unnoticed. To update one:

```bash
git -C third_party/<app> fetch origin && git -C third_party/<app> checkout <commit>
git add third_party/<app>     # then run the tests below and commit
```

## Multiple warehouses

ERPNext itself keeps stock per warehouse (a warehouse tree with group warehouses), moves it with a Material Transfer stock entry, reserves it per warehouse when a Sales Order is submitted, and offers Pick Lists and Putaway Rules inside a warehouse. I found no open-source warehouse-management app for Frappe, only paid ones, so none is bundled; for multi-channel warehouse operations the Unicommerce connector is the closest.

What differs is how each channel decides which warehouse an order is booked against, and which warehouses it is sent stock from:

| Channel | Order is booked to | Stock is sent from |
| --- | --- | --- |
| Flipkart | the warehouse mapped to the shipment's Flipkart location, else the Default Warehouse | every mapped warehouse, each to its own location; a group warehouse sends the sum of its children |
| Shopify | the one warehouse in Shopify Settings | each mapped Shopify location, if stock updates to Shopify are on |
| Amazon | the one warehouse in Amazon SP API Settings | nothing: the connector never pushes stock to Amazon |
| Unicommerce | the warehouse mapped to the order's facility, else the channel's warehouse | each mapped facility |

Stock sent to a channel is what is **available**: actual quantity less what open Sales Orders reserve in that warehouse. For that to hold across channels, every channel's orders must reach ERPNext as submitted Sales Orders, and a warehouse that two channels draw from can be oversold in the minutes between an order and the next sync. Give each channel its own warehouse where you can, and keep shared stock in a parent group.

The Flipkart connector's multi-warehouse behaviour is covered by tests: separate locations, only the changed warehouse being sent again, transfers between warehouses, reservations per warehouse, and group warehouses.

## Shipping labels

Open a Delivery Note and choose **Create > Shipment** (added by `bwh_shipping`): it drafts a Shipping Request, which you book with the provider (Shiprocket or AfterShip) to get the AWB and label. Tracking comes back by webhook. Set up a provider first under **Shipping Provider Profile**; see the app's [documentation](https://docs.bwh.tech/bwh-shipping/get-started/overview). Labels for Flipkart orders still come from Flipkart; the Flipkart connector does not create them.

## Tally

Installed, and inert until you open **Tally Settings**, tick *Enabled* and give it the *Tally Server Host* and *Port* of Tally's XML gateway (Gateway of Tally > F12 > Advanced Configuration > *Allow XML Request*). Know these before you switch it on:

- **Access.** The app's own API lets any logged-in user create Tally ledgers and vouchers and push any Sales Invoice by name. `marketplace_guard` closes that by requiring the System Manager or Accounts Manager role, for session, API-key and OAuth callers alike. This is tested over real HTTP. Keep `marketplace_guard` installed as long as `tally_erpnext` is, unless upstream adds role checks of its own.
- **Names with `&` or `<`.** The app builds its XML requests by string formatting without escaping, so a customer such as `A & B Traders` makes the request fail, and a hostile name could alter the request. Check your party names before pushing in bulk.
- **Auto sync runs every minute.** With *Enable Auto Sync* on, a job pulls ledgers, stock items and vouchers from Tally into the app's own Tally Ledger, Tally Stock Item and Tally Voucher records (not directly into Customers or Items). Pushing in the other direction, such as a Sales Invoice, is done with the app's buttons and API or with Server Scripts. Try it on a copy of your data first.
- **No encryption to Tally.** The connection is plain HTTP, so keep Tally on a trusted network or behind a VPN.
- **Not run against Tally itself.** Tally is Windows software and there was none to connect to. What is tested is that the app installs on this ERPNext version, that its scheduled job does nothing while disabled, and the guard.

## What has been tested

On a fresh ERPNext 17 site, with all five apps installed by `install.sh`:

- Flipkart: 40 unit tests and 45 site tests, against a fake Flipkart.
- `marketplace_guard`: 9 tests, including real HTTP requests.
- `bwh_shipping`: its own 30 tests pass.
- `ecommerce_integrations`: the upstream suite passes, 119 tests in 29 modules with none failing (upstream skips 3 of them). It only passes on a fresh database prepared the way its CI prepares one, and only once per database, because the tests commit data that a second run trips over. [`run-upstream-tests.sh`](./run-upstream-tests.sh) does that: it builds a throwaway site, prepares it, runs the suite and drops the site (`MARIADB_ROOT_PASSWORD=... marketplace/run-upstream-tests.sh <bench>`, about five minutes). What it prepares, and why:
  - the company "Wind Power LLC" is created as the Indian (INR) one before anything else, because ERPNext's own test records define a US-dollar company with the same name and whichever exists first wins;
  - *Allow same Item to be added multiple times in a transaction* is switched on, because Unicommerce sends one order row per unit and ERPNext otherwise refuses the second row;
  - INR/USD exchange rates are added, because ERPNext otherwise fetches them from the internet;
  - the Unicommerce Channel test records are committed, because Frappe 17 rolls each test class back;
  - the customer test runs first, because it asks for "the last customer created" and gets someone else's if another test has already created and committed that customer.

  None of these are faults in the connectors; they are assumptions the tests make about their database.
- In a real browser (Chromium driving the desk of a fresh site with every app installed), 38 scripted checks pass. They cover: the Flipkart Settings form, its validation and all five Actions; the Tally, Shiprocket, AfterShip, Shopify, Amazon and Unicommerce screens; Create > Shipment on a Delivery Note; the Tally guard for an API key and for three kinds of login (Administrator, Accounts Manager, Sales User); a Flipkart import run against a fake Flipkart, with the Sales Orders, address, stock sent, error logs from a background worker, and a cancellation. This found two bugs that the automated tests missed, both fixed: **Sync Orders** failed with HTTP 417 when clicked, and imported orders showed no Flipkart order ID on the form or in the list.
- Nothing has been run against real Shopify, Amazon, Flipkart, Shiprocket, AfterShip, Unicommerce or Tally accounts.

```bash
bench --site <site> run-tests --app flipkart_integration
bench --site <site> run-tests --app marketplace_guard
bench --site <site> run-tests --app bwh_shipping
```

## Configure each channel

- **Shopify, Amazon, Unicommerce**: open the settings doctype of each and follow the [integration documentation](https://docs.frappe.io/erpnext/erpnext_integration). Amazon needs SP-API seller credentials, Shopify needs a custom app created in your Shopify admin.
- **Flipkart**: open **Flipkart Settings** and follow [`flipkart_integration/README.md`](../flipkart_integration/README.md).
