# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters: dict | None = None):
	filters = filters or {}

	if filters.get("survey_template"):
		return single_survey_report(filters)

	return multiple_surveys_report(filters)


def match_sales_orders(value):
	if not value:
		return None

	orders = set()
	orders.update(frappe.get_all("Sales Order", filters={"akd_delivery_center": value}, pluck="name"))
	orders.update(frappe.get_all("Sales Order Item", filters={"item_code": value}, pluck="parent"))

	return list(orders)


def get_survey_responses(filters):
	document_type = filters.get("document_type") or "Sales Order"
	conditions = {"document_type": document_type}

	if filters.get("customer"):
		conditions["customer"] = filters["customer"]

	if filters.get("survey_template"):
		conditions["survey_template"] = filters["survey_template"]

	if document_type == "Sales Order":
		sales_orders = match_sales_orders(filters.get("sales_order"))
		if sales_orders:
			conditions["document_name"] = ["in", sales_orders]
		
	if filters.get("document_name"):
		conditions["document_name"] = ["in", filters["document_name"]]

	return frappe.get_all(
		"WhatsApp Survey Response",
		filters=conditions,
		fields=["name", "customer", "document_type", "document_name as document", "survey_template"],
	)


def multiple_surveys_report(filters):
	columns = [
		{"fieldname": "survey_template", "label": _("Survey"), "fieldtype": "Link", "options": "WhatsApp Survey Response", "width": 160},
		{"fieldname": "question", "label": _("Question"), "fieldtype": "Data", "width": 420},
		{"fieldname": "answer", "label": _("Answer"), "fieldtype": "Data", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 140},
		{"fieldname": "document_type", "label": _("Document Type"), "fieldtype": "Link", "options": "DocType", "width": 120},
		{"fieldname": "document", "label": _("Document"), "fieldtype": "Dynamic Link", "options": "document_type", "width": 140},
	]

	responses = get_survey_responses(filters)
	if not responses:
		return columns, []

	content_filters = {"parent": ["in", [response.name for response in responses]]}
	if filters.get("question"):
		content_filters["question"] = ["like", f"%{filters['question']}%"]
	if filters.get("answer"):
		content_filters["answer"] = ["like", f"%{filters['answer']}%"]

	content = frappe.get_all(
		"Survey Response Content",
		filters=content_filters,
		fields=["parent", "question", "answer"],
	)

	response_by_name = {response.name: response for response in responses}

	content_by_survey = {}
	for row in content:
		response = response_by_name[row.parent]
		questions = content_by_survey.setdefault(response.survey_template, {})
		questions.setdefault(row.question, []).append((response, row))

	data = []
	for survey_template, questions in content_by_survey.items():
		data.append({"survey_template": survey_template, "bold": 1})
		for question, rows in questions.items():
			data.append({"question": question})
			for response, row in rows:
				data.append({
					"survey_template": response.name,
					"customer": response.customer,
					"document_type": response.document_type,
					"document": response.document,
					"answer": row.answer
				})
				
		data.append({
					"survey_template": "",
					"customer": "",
					"document_type": "",
					"document": "",
					"answer": ""
		})
	return columns, data


def single_survey_report(filters):
	question_col = {"fieldname": "question", "label": _("Question"), "fieldtype": "HTML", "width": 500}

	responses = get_survey_responses(filters)
	if not responses:
		return [question_col], []

	content = frappe.get_all(
		"Survey Response Content",
		filters={"parent": ["in", [response.name for response in responses]]},
		fields=["question", "answer"],
	)

	answer_fieldnames = {}
	counts = {}
	for row in content:
		if row.answer not in answer_fieldnames:
			answer_fieldnames[row.answer] = f"answer_{len(answer_fieldnames)}"
		question_counts = counts.setdefault(row.question, {})
		question_counts[row.answer] = question_counts.get(row.answer, 0) + 1

	columns = [question_col] + [
		{"fieldname": fieldname, "label": answer, "fieldtype": "Data", "width": 120}
		for answer, fieldname in answer_fieldnames.items()
	]

	data = []
	for question, answer_counts in counts.items():
		row = {"question": f"<b>{question}</b>"}
		for answer, fieldname in answer_fieldnames.items():
			row[fieldname] = answer_counts.get(answer, 0)
		data.append(row)

	return columns, data
