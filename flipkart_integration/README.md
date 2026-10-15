# Flipkart Integration for ERPNext

A Frappe app that connects an ERPNext site to a Flipkart Marketplace seller account: Flipkart orders arrive as Sales Orders, and ERPNext stock is sent back to Flipkart.

It is built on [`ecommerce_integrations`](https://github.com/frappe/ecommerce_integrations), the app that provides the Shopify, Amazon and Unicommerce connectors, and uses its `Ecommerce Item` and `Ecommerce Integration Log` doctypes. Install that app first (see [`marketplace/`](../marketplace/README.md)).

## What it does

| Direction | What | How |
| --- | --- | --- |
| Flipkart → ERPNext | Items | `Sync Listings` records every Flipkart listing as a **Flipkart Listing** and links it to the ERPNext Item with the same Item Code (the seller SKU). Listings whose SKU matches nothing wait for you to [map them by hand](#mapping-products). It can also create the Items that are missing. |
| Flipkart → ERPNext | Orders | Every shipment that is waiting to be dispatched becomes one Sales Order, with the delivery address and a warehouse taken from the shipment's Flipkart location. |
| Flipkart → ERPNext | Cancellations | A shipment cancelled on Flipkart cancels its Sales Order (a draft is deleted). If a Delivery Note or invoice already exists, the order is left alone and the failure is logged for you to deal with. |
| ERPNext → Flipkart | Stock | Available stock (actual less reserved) of each mapped warehouse is sent to its Flipkart location whenever the warehouse's stock changes. |

Orders and stock are checked every five minutes by the scheduler, and each only runs once its configured frequency (15 minutes by default) has passed. Listings are linked once a day.

Failures are written to **Ecommerce Integration Log**, one log per failing shipment or SKU. A shipment that failed is still missing from ERPNext, so the next run tries it again, and its log is marked resolved when it succeeds.

## Setup

1. Install the app (`marketplace/install.sh` does this) and run `bench migrate`.
2. In the [Flipkart Seller API Developer Admin portal](https://seller.flipkart.com/api-docs/FMSAPI.html), register an application for your own seller account and copy its Application ID and Secret.
3. Open **Flipkart Settings**, tick **Enable Flipkart**, enter the credentials and choose a **Company**.
4. Add one row to **Warehouse Mapping** per Flipkart location: the location ID (seller panel, Manage Profile, Manage Location) and the ERPNext warehouse that holds its stock.
5. Save, then use **Actions > Check Connection**.
6. Run **Actions > Sync Listings** so Flipkart SKUs are linked to your Items, then open **Actions > Map Products** to deal with the SKUs that did not match. Stock is only sent for linked items.
7. Tick **Sync Orders from Flipkart** and **Sync Stock to Flipkart** and set **Import Orders Placed From**. Save.

## Things to know

- **One customer.** Flipkart does not share buyer contact details, so every order is booked against one customer (`Flipkart Customer` unless you pick another). The buyer's name and address are kept on an Address linked to that customer, one per shipment.
- **On the Sales Order.** The Flipkart order ID, shipment ID and total price sit on the Details tab, only on Flipkart orders, and the order ID is a column and a filter in the Sales Order list.
- **One Sales Order per shipment.** An order with items in several shipments gives several Sales Orders that share the Flipkart order ID. The shipment ID is what prevents an order from being imported twice.
- **One row per item.** Order items for the same SKU in one shipment are combined into a single row (quantities added, price averaged by quantity), because ERPNext does not allow the same item on two rows of a Sales Order unless *Allow same Item to be added multiple times in a transaction* is on in Selling Settings. The row lists every Flipkart order item ID it covers.
- **Prices.** The rate comes from `priceComponents.customerPrice`, or `sellingPrice` when that is missing. Flipkart's documentation does not say whether the price of an order item is per unit or for the whole quantity; **Flipkart Prices Are** in the settings picks one. For quantities above 1, compare a Sales Order against the order in the Flipkart panel before relying on it. Each Sales Order also stores **Flipkart Total Price** (what the buyer paid, shipping included) to make that comparison easy.
- **Taxes.** The tax template in the settings is applied to every order. If Flipkart's prices include GST, use a template whose rows are marked *Included in Print Rate*. Shipping charged to the buyer is added as a separate row when a **Shipping Charge Account** is set.
- **Stock numbers.** Stock is sent as an absolute number, so it is always consistent with ERPNext as long as every channel's orders reach ERPNext. An order placed on Flipkart is only reserved in ERPNext after the next order sync, so there is a short window in which stock can be oversold.
- **Orders dispatched elsewhere.** Only shipments waiting for dispatch are imported. A shipment that was already shipped before the first sync is skipped; **Sync Orders Including Dispatched** imports those too.
- **Group warehouses.** A Flipkart location may map to a group warehouse for stock (the stock of all its children is added up), but Sales Orders need a leaf warehouse, so set a **Default Warehouse** for those orders.

## Mapping products

Flipkart calls a product by a SKU and a name you chose there, and ERPNext calls it by an Item Code and an Item Name. They rarely agree. By default a Flipkart SKU is matched to the Item whose **Item Code is the same**; everything else is mapped by hand:

1. **Fetch Listings from Flipkart** (the button on the **Flipkart Listing** list, or **Actions > Sync Listings** in Flipkart Settings) fills the list with one row per Flipkart SKU, showing its name on Flipkart and, for the rows that matched, the ERPNext Item. **Actions > Map Products** opens the rows that are still **Not mapped**.
2. Open a row and choose the **ERPNext Item**. That is the whole mapping: orders for the SKU are booked against that item, and the item's stock is sent to Flipkart under that SKU.
3. For many at once, export the list (Menu > Export, ticking the ID and ERPNext Item columns), fill the ERPNext Item column in a spreadsheet and import it back with **Update Existing Records**. A file with one bad item is refused as a whole, and the message names the row.

What to know:

- **A mapping you made always wins.** The daily listing sync never replaces it, not even by an item whose code equals the SKU. Clearing the ERPNext Item returns the SKU to automatic matching.
- **Orders for an unmapped SKU wait.** The order is not imported, an error is logged naming every SKU in it, and the SKUs are added to the Flipkart Listing list. Map them and the order comes in on the next order sync (every 15 minutes by default, or at once with **Actions > Sync Orders**).
- **Several Flipkart SKUs can stand for one item** (the same product in two colours listed separately, say). They are all sent the item's stock, and an order holding both gets one row. The reverse is not possible: one SKU is one item.
- **A Flipkart product ID can be mapped to an item once.** A second SKU with the same product ID for the same item is refused.
- **One SKU is one unit of the item.** A listing that sells a pack of three is not supported; the quantity would be booked as ordered.
- Template items and disabled items cannot be chosen.
- The report **Item Platform Mapping** (find it in the search bar) shows, for every ERPNext item, the SKU it has on each platform that uses the shared Ecommerce Item records, and with **Not Linked On** it lists the items that are missing on one platform. The Shopify and Unicommerce connectors can be mapped by hand in the same way; see [`marketplace/README.md`](../marketplace/README.md#matching-products-across-platforms).

## Not covered

Dispatching, packing and labels, returns, price updates, creating new listings on Flipkart, and bundles (one Flipkart SKU standing for several units of an item). Orders and stock only.

## Tests

```bash
# No site needed: API client and payload mapping
cd flipkart_integration
python -m pytest flipkart_integration/tests/test_api_client.py flipkart_integration/tests/test_mapping.py

# With a site: doctype, order, stock and listing sync
bench --site <site> run-tests --app flipkart_integration
```

The tests use recorded response shapes and a fake HTTP session. They have not been run against Flipkart itself.

## Licence

GNU General Public License v3.
