app_name = "flipkart_integration"
app_title = "Flipkart Integration"
app_publisher = "Flipkart Integration contributors"
app_description = "Flipkart Marketplace connector for ERPNext: orders in, stock out"
app_email = ""
app_license = "GNU General Public License (v3)"

required_apps = ["frappe/erpnext", "frappe/ecommerce_integrations"]

after_install = "flipkart_integration.install.after_install"
after_migrate = "flipkart_integration.install.after_migrate"

before_tests = "flipkart_integration.tests.before_tests.before_tests"

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"flipkart_integration.flipkart.order.sync_new_orders",
			"flipkart_integration.flipkart.inventory.update_inventory_on_flipkart",
		],
	},
	"daily_long": [
		"flipkart_integration.flipkart.product.sync_listings",
	],
}
