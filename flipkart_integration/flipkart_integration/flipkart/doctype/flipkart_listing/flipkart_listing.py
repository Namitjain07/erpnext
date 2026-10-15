import frappe
from frappe import _
from frappe.model.document import Document

from flipkart_integration.flipkart.constants import SETTINGS_DOCTYPE
from flipkart_integration.flipkart.product import ensure_ecommerce_item, remove_ecommerce_item


class FlipkartListing(Document):
	"""One Flipkart SKU and the ERPNext item it stands for.

	Saving the listing keeps the Ecommerce Item link in step, and that link is what the order and
	stock sync read, so choosing an item here is all it takes to map a product.
	"""

	def validate(self):
		self.sku = (self.sku or "").strip()
		self.fsn = (self.fsn or "").strip()
		self.validate_item()
		self.set_mapping_source()

	def validate_item(self):
		"""Only what a person chooses is checked; the sync links whatever item has the SKU as its code."""
		if not self.erpnext_item or self.flags.from_sync:
			return

		item = frappe.db.get_value("Item", self.erpnext_item, ["disabled", "has_variants"], as_dict=True)
		if item.has_variants:
			frappe.throw(
				_("Item {0} is a template. Map the Flipkart SKU to one of its variants.").format(
					frappe.bold(self.erpnext_item)
				)
			)
		if item.disabled:
			frappe.throw(_("Item {0} is disabled.").format(frappe.bold(self.erpnext_item)))

	def set_mapping_source(self):
		"""A person choosing or clearing the item makes it a manual mapping; the sync sets its own."""
		if self.flags.from_sync or not self.has_value_changed("erpnext_item"):
			return
		self.mapping_source = "Manual" if self.erpnext_item else ""

	def on_update(self):
		if self.erpnext_item:
			ensure_ecommerce_item(self.sku, self.fsn, self.erpnext_item)
		else:
			remove_ecommerce_item(self.sku)

	def on_trash(self):
		remove_ecommerce_item(self.sku)


@frappe.whitelist()
def fetch_listings():
	"""Pull the seller's listings from Flipkart, so there is a row to map for each SKU."""
	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	if not settings.is_enabled():
		frappe.throw(_("Enable Flipkart in Flipkart Settings first."))
	settings.sync_listings_now()
