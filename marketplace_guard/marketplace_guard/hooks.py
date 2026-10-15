app_name = "marketplace_guard"
app_title = "Marketplace Guard"
app_publisher = "Marketplace Guard contributors"
app_description = (
	"Access control for third-party marketplace and accounting apps bundled with this ERPNext fork"
)
app_email = ""
app_license = "GNU General Public License (v3)"

required_apps = ["frappe/erpnext"]

auth_hooks = ["marketplace_guard.guards.restrict_tally_api"]
