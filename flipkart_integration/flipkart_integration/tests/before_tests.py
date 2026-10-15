import frappe
from erpnext.setup.utils import enable_all_roles_and_domains
from frappe.utils import now_datetime

COMPANY = "Wind Power LLC"
TAX_ACCOUNT = "Output Tax GST"


def before_tests():
	frappe.clear_cache()

	if not frappe.get_list("Company"):
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		year = now_datetime().year
		setup_complete(
			{
				"currency": "INR",
				"full_name": "Test User",
				"company_name": COMPANY,
				"timezone": "Asia/Kolkata",
				"company_abbr": "WP",
				"industry": "Manufacturing",
				"country": "India",
				"fy_start_date": f"{year}-01-01",
				"fy_end_date": f"{year}-12-31",
				"language": "english",
				"company_tagline": "Testing",
				"email": "test@erpnext.com",
				"password": "test",
				"chart_of_accounts": "Standard",
				"domains": ["Manufacturing"],
			}
		)

	frappe.db.set_value("Stock Settings", None, "auto_insert_price_list_rate_if_missing", 0)
	enable_all_roles_and_domains()
	create_tax_account()
	frappe.db.commit()


def create_tax_account():
	if frappe.db.exists("Account", {"company": COMPANY, "account_name": TAX_ACCOUNT}):
		return

	parent = (
		frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Tax", "is_group": 1})
		or "Duties and Taxes - WP"
	)
	frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": TAX_ACCOUNT,
			"is_group": 0,
			"company": COMPANY,
			"root_type": "Liability",
			"report_type": "Balance Sheet",
			"account_currency": "INR",
			"parent_account": parent,
			"account_type": "Tax",
			"tax_rate": 18,
		}
	).insert()
