// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt
frappe.query_reports["Survey Responses"] = {
	"onload": function(report) {
		console.log("in js, seen");
	},

	filters: [
		{
			"fieldname": "survey_template",
			"label": __("Survey"),
			"fieldtype": "Link",
			"options": "WhatsApp Templates"
		},
		{
			"fieldname": "customer",
			"label": __("Customer"),
			"fieldtype": "Link",
			"options": "Customer"
		},
		{
			"fieldname": "question",
			"label": __("Question"),
			"fieldtype": "Data",
			"depends_on": "eval:!doc.survey_template"
		},
		{
			"fieldname": "answer",
			"label": __("Answer"),
			"fieldtype": "Data",
			"depends_on": "eval:!doc.survey_template"
		},
		{
			"fieldname": "document_type",
			"label": __("Document Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"default": "Sales Order"
		},
		{
			"fieldname": "document_name",
			"label": __("Document Name"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				let document_type = frappe.query_report.get_filter_value("document_type");
				if (!document_type) return [];
				return frappe.db.get_link_options(document_type, txt);
			}
		},
		{
			"fieldname": "sales_order",
			"label": __("Item Code / Delivery Center"),
			"fieldtype": "Data",
			"depends_on": "eval:doc.document_type==\"Sales Order\""
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) {
			value = value.bold();
		}
		return value;
	},
};
