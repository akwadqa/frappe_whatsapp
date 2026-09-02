import frappe
from frappe import _

SENT_STATUSES = ["sent", "delivered", "read", "Success"]
FAILED_STATUSES = ["failed", "Failed"]
QUEUED_STATUSES = ["queued", "Queued"]

STATUS_LABELS = {
    "queued": _("Queued"),
    "Queued": _("Queued"),
    "sent": _("Sent"),
    "Success": _("Sent"),
    "delivered": _("Delivered"),
    "read": _("Read"),
    "failed": _("Failed"),
    "Failed": _("Failed"),
}

STATUS_FILTER_MAP = {
    "Queued": QUEUED_STATUSES,
    "Sent": SENT_STATUSES,
    "Failed": FAILED_STATUSES,
}


def execute(filters=None):
    filters = frappe._dict(filters or {})

    columns = get_columns()
    data = get_data(filters)
    counts = get_counts(filters)
    report_summary = get_report_summary(counts)

    return columns, data, None, None, report_summary


def get_columns():
    return [
        {
            "fieldname": "name",
            "label": _("Message"),
            "fieldtype": "Link",
            "options": "WhatsApp Message",
            "width": 150,
        },
        {
            "fieldname": "bulk_message_reference",
            "label": _("Campaign"),
            "fieldtype": "Link",
            "options": "Bulk WhatsApp Message",
            "width": 150,
        },
        {
            "fieldname": "to",
            "label": _("Recipient"),
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "fieldname": "status_label",
            "label": _("Status"),
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "fieldname": "error_message",
            "label": _("Failure Reason"),
            "fieldtype": "Data",
            "width": 350,
        },
        {
            "fieldname": "creation",
            "label": _("Sent On"),
            "fieldtype": "Datetime",
            "width": 160,
        },
    ]


def get_data(filters):
    query_filters = {}

    if filters.get("campaign"):
        query_filters["bulk_message_reference"] = filters.campaign

    if filters.get("status"):
        query_filters["status"] = ["in", STATUS_FILTER_MAP.get(filters.status, [filters.status])]

    if filters.get("message_id"):
        query_filters["name"] = ["like", f"%{filters.message_id}%"]

    if filters.get("recipient"):
        query_filters["to"] = ["like", f"%{filters.recipient}%"]

    rows = frappe.get_all(
        "WhatsApp Message",
        filters=query_filters,
        fields=["name", "bulk_message_reference", "to", "status", "error_message", "creation"],
        order_by="creation desc",
    )

    for row in rows:
        row["status_label"] = STATUS_LABELS.get(row.status, row.status) or _("Unknown")
        if row.status not in FAILED_STATUSES:
            row["error_message"] = ""

    return rows


def get_counts(filters):
    query_filters = {}

    if filters.get("campaign"):
        query_filters["bulk_message_reference"] = filters.campaign

    total = frappe.db.count("WhatsApp Message", query_filters)
    sent = frappe.db.count("WhatsApp Message", {**query_filters, "status": ["in", SENT_STATUSES]})
    failed = frappe.db.count("WhatsApp Message", {**query_filters, "status": ["in", FAILED_STATUSES]})
    queued = frappe.db.count("WhatsApp Message", {**query_filters, "status": ["in", QUEUED_STATUSES]})
    # Messages whose status doesn't fall into any known bucket (blank/NULL,
    # or a stray non-standard value) - kept visible instead of silently
    # vanishing from the totals.
    other = total - (sent + failed + queued)

    return frappe._dict(total=total, sent=sent, failed=failed, queued=queued, other=other)


def get_report_summary(counts):
    delivered = round((counts.sent / counts.total) * 100, 1) if counts.total else 0

    summary = [
        {"value": counts.total, "label": _("Total Messages"), "datatype": "Int", "indicator": "blue"},
        {"value": counts.sent, "label": _("Sent"), "datatype": "Int", "indicator": "green"},
        {"value": counts.failed, "label": _("Failed"), "datatype": "Int", "indicator": "red"},
        {"value": counts.queued, "label": _("Queued"), "datatype": "Int", "indicator": "orange"},
        {"value": delivered, "label": _("Success Rate"), "datatype": "Percent", "indicator": "green" if delivered >= 80 else "orange"},
    ]

    if counts.other:
        summary.append(
            {"value": counts.other, "label": _("Other / Unknown"), "datatype": "Int", "indicator": "gray"}
        )

    return summary
