# Marketplace Guard

Small Frappe app that adds access control the bundled third-party apps lack.

## Tally

[`tally_erpnext`](https://github.com/svnix-solutions/tally_erpnext) exposes about a dozen
`@frappe.whitelist()` functions without a role check. Any logged-in user could create ledgers and
vouchers in your Tally company, or push any Sales Invoice to it by name.

An auth hook runs once Frappe knows who is calling, whether by session cookie, API key or OAuth, and
rejects calls to `tally_erpnext.*` unless the user is a **System Manager** or **Accounts Manager**. The
scheduler and other server-side callers are not affected.

## Tests

```bash
bench --site <site> run-tests --app marketplace_guard
```

## Licence

GNU General Public License v3.
