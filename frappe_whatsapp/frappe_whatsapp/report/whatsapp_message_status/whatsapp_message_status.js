frappe.query_reports["WhatsApp Message Status"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_days(frappe.datetime.get_today(), -7),
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
        },
        {
            "fieldname": "campaign",
            "label": __("Campaign"),
            "fieldtype": "Link",
            "options": "Bulk WhatsApp Message",
        },
        {
            "fieldname": "status",
            "label": __("Status"),
            "fieldtype": "Select",
            "options": "\nQueued\nSent\nFailed",
        },
        {
            "fieldname": "message_id",
            "label": __("Message ID"),
            "fieldtype": "Data",
        },
        {
            "fieldname": "recipient",
            "label": __("Recipient Number"),
            "fieldtype": "Data",
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldname === "status_label") {
            const color = {
                queued: "orange", Queued: "orange",
                sent: "green", Success: "green", delivered: "green", read: "green",
                failed: "red", Failed: "red",
            }[data.status] || "gray";
            value = `<span class="indicator-pill ${color} filterable" data-value="${data.status_label}">
                <span>${data.status_label}</span>
            </span>`;
        }

        if (column.fieldname === "error_message" && data.error_message) {
            value = `<span style="color: var(--red-600, #d1242f);">${frappe.utils.escape_html(
                data.error_message
            )}</span>`;
        }

        return value;
    },
};
