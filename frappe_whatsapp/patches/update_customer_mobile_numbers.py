import frappe

def execute():
    try:
        """Backfill custom_mobile_no on Customer from linked User mobile_no."""
        customers = frappe.get_all("Customer", fields=["name", "akd_user"])

        for c in customers:
            if not c.akd_user:
                continue
            mobile = frappe.db.get_value("User", c.akd_user, "mobile_no")
            if mobile:
                frappe.db.set_value("Customer", c.name, "custom_mobile_no", mobile)

        frappe.db.commit()

    except Exception:
        frappe.log_error("Customer Mobile No.: Failed to backfill", frappe.get_traceback())