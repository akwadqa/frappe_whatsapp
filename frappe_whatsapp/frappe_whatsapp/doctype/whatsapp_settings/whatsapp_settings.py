# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

QR_CARD_TEMPLATE_FIELDS = ("qr_card_template_en", "qr_card_template_ar")


class WhatsAppSettings(Document):
	def validate(self):
		self.validate_qr_card_templates()

	def validate_qr_card_templates(self):
		"""Meta rejects the QR card unless the template is approved and has an image header."""
		for fieldname in QR_CARD_TEMPLATE_FIELDS:
			template = self.get(fieldname)
			if not template:
				continue

			status, header_type = frappe.db.get_value("WhatsApp Templates", template, ["status", "header_type"])
			label = self.meta.get_label(fieldname)
			if status != "APPROVED":
				frappe.throw(
					_("{0}: template {1} has status {2}. It must be APPROVED.").format(label, template, status),
					exc=frappe.ValidationError,
					title=_("QR Card Template Not Approved"),
				)
			if header_type != "IMAGE":
				frappe.throw(
					_("{0}: template {1} must have an IMAGE header to carry the QR card.").format(label, template),
					exc=frappe.ValidationError,
					title=_("QR Card Template Has No Image Header"),
				)
