import json
import frappe
from frappe import _
from frappe.utils import cint, nowdate, add_days


@frappe.whitelist()
def get_progress(name):
    """Get progress for a bulk message"""
    doc = frappe.get_doc("Bulk WhatsApp Message", name)
    return doc.get_progress()

@frappe.whitelist()
def retry_failed(name):
    """Retry failed messages"""
    doc = frappe.get_doc("Bulk WhatsApp Message", name)
    doc.retry_failed()
    return True

@frappe.whitelist()
def import_recipients(list_name, doctype, mobile_field, customer=None, name_field=None, filters=None, limit=None, data_fields=None):
    """Import recipients from a DocType"""
    if filters and isinstance(filters, str):
        filters = json.loads(filters)

    if data_fields and isinstance(data_fields, str):
        data_fields = json.loads(data_fields)
        
    doc = frappe.get_doc("WhatsApp Recipient List", list_name)
    count = doc.import_list_from_doctype(doctype, mobile_field, customer, name_field, filters, limit, data_fields)
    doc.save()
    
    return count

@frappe.whitelist()
def get_customers_for_import(recipients_type, days_since_last_order=None, registered_in_the_last_days=None):
    try:
        customers = []

        if recipients_type == "All Customers":
            customers = [r.name for r in frappe.get_all(
                "Customer",
                filters={"disabled": 0},
                fields=["name"]
            )]

        elif recipients_type == "Inactive Customers":
            days = int(days_since_last_order)
            start_date = add_days(nowdate(), -days)
            end_date = nowdate()

            past_customers = set(r[0] for r in frappe.db.sql("""
                SELECT DISTINCT customer FROM (
                    SELECT customer, transaction_date as txn_date FROM `tabSales Order`
                    WHERE docstatus = 1 AND customer IS NOT NULL
                    UNION ALL
                    SELECT customer, posting_date as txn_date FROM `tabSales Invoice`
                    WHERE docstatus = 1 AND customer IS NOT NULL
                ) t WHERE txn_date < %(start_date)s
            """, {"start_date": start_date}))

            recent_customers = set(r[0] for r in frappe.db.sql("""
                SELECT DISTINCT customer FROM (
                    SELECT customer, transaction_date as txn_date FROM `tabSales Order`
                    WHERE docstatus = 1 AND customer IS NOT NULL
                    UNION ALL
                    SELECT customer, posting_date as txn_date FROM `tabSales Invoice`
                    WHERE docstatus = 1 AND customer IS NOT NULL
                ) t WHERE txn_date BETWEEN %(start_date)s AND %(end_date)s
            """, {"start_date": start_date, "end_date": end_date}))

            customers = list(past_customers - recent_customers)

        elif recipients_type == "Customers Without Transactions":
            customers = [r[0] for r in frappe.db.sql("""
                SELECT name FROM `tabCustomer`
                WHERE name NOT IN (
                    SELECT DISTINCT customer FROM `tabSales Order`
                    WHERE docstatus = 1 AND customer IS NOT NULL
                )
            """)]

        elif recipients_type == "Recently Registered Customers":
            days = int(registered_in_the_last_days)
            cutoff_date = add_days(nowdate(), -days + 1)
            customers = [r.name for r in frappe.get_all(
                "Customer",
                filters=[["Customer", "creation", ">=", cutoff_date]],
                fields=["name"]
            )]

        if not customers:
            return []

        customers_info = frappe.get_all(
            "Customer",
            filters={"name": ["in", customers]},
            fields=["name", "customer_name", "custom_mobile_no"]
        )

        result = []
        for c in customers_info:
            mobile = (c.get("custom_mobile_no") or "").strip()
            if not mobile:
                continue
            result.append({
                "customer": c.get("name"),
                "recipient_name": c.get("customer_name") or c.get("name"),
                "mobile_number": mobile,
            })

        return result

    except Exception as e:
        frappe.log_error(title="Error in get_customers_for_import", message=str(e))
        frappe.throw(_("An error occurred while importing customers."))
        

@frappe.whitelist()
def schedule_bulk_messages():
    """Background job to process bulk WhatsApp messages"""
    # Find queued bulk messages with recipient counts less than sent counts
    bulk_messages = frappe.get_all(
        "Bulk WhatsApp Message", 
        filters={
            "status": "Queued",
            "docstatus": 1
        },
        fields=["name", "recipient_count", "sent_count"]
    )
    
    for bulk in bulk_messages:
        # Skip if all messages have been sent
        if cint(bulk.sent_count) >= cint(bulk.recipient_count):
            frappe.db.set_value("Bulk WhatsApp Message", bulk.name, "status", "Completed")
            continue
            
        # Check for failed messages
        failed_count = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": bulk.name,
            "status": "Failed"
        })
        
        # If all messages are either sent or failed
        if cint(bulk.sent_count) - failed_count + cint(failed_count) >= cint(bulk.recipient_count):
            if failed_count > 0:
                frappe.db.set_value("Bulk WhatsApp Message", bulk.name, "status", "Partially Failed")
            else:
                frappe.db.set_value("Bulk WhatsApp Message", bulk.name, "status", "Completed")
