MODULE_NAME = "Flipkart"
SETTINGS_DOCTYPE = "Flipkart Settings"

ORDER_ID_FIELD = "flipkart_order_id"
SHIPMENT_ID_FIELD = "flipkart_shipment_id"
ORDER_TOTAL_FIELD = "flipkart_total_price"
ORDER_ITEM_ID_FIELD = "flipkart_order_item_id"

PRE_DISPATCH_STATES = ("APPROVED", "PACKING_IN_PROGRESS", "PACKED", "READY_TO_DISPATCH")
POST_DISPATCH_STATES = ("SHIPPED", "DELIVERED", "PICKUP_COMPLETE")
CANCELLATION_TYPES = ("marketplaceCancellation", "sellerCancellation", "buyerCancellation")
CANCELLATION_LOOKBACK_DAYS = 3
LISTING_STATES = ("ACTIVE", "INACTIVE")
