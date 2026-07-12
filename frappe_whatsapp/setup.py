import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
    create_custom_fields(get_custom_fields(), ignore_validate=True, update=True)


def before_uninstall():
    delete_custom_fields(get_custom_fields())


def get_custom_fields():
    return {
        "Customer": [
            {
                "fieldname": "customer_whatsapp_user",
                "fieldtype": "Link",
                "label": _("WhatsApp User"),
                "insert_after": "customer_name",
                "options": "User",
            },
            {
                "fieldname": "custom_mobile_no",
                "fieldtype": "Data",
                "label": _("Mobile No"),
                "insert_after": "customer_whatsapp_user",
                "fetch_from": "customer_whatsapp_user.mobile_no",
                "read_only": 1,
            },
        ],
        "Sales Order": [
            {
                "fieldname": "custom_mobile_no",
                "fieldtype": "Data",
                "label": _("Mobile No"),
                "insert_after": "customer_name",
                "fetch_from": "customer.custom_mobile_no",
                "read_only": 1,
            },
        ],
        "Delivery Note": [
            {
                "fieldname": "custom_mobile_no",
                "fieldtype": "Data",
                "label": _("Mobile No"),
                "insert_after": "customer_name",
                "fetch_from": "customer.custom_mobile_no",
                "read_only": 1,
            },
        ],
    }


def delete_custom_fields(custom_fields: dict):
    for doctype, fields in custom_fields.items():
        frappe.db.delete(
            "Custom Field",
            {
                "fieldname": ("in", [field["fieldname"] for field in fields]),
                "dt": doctype,
            },
        )
        frappe.clear_cache(doctype=doctype)