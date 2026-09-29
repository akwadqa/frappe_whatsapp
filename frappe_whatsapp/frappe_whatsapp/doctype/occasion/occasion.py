# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
# from frappe import _
from frappe.model.document import Document


class Occasion(Document):
	def validate(self):
		self.set_qr_delivery()

	def set_qr_delivery(self):
		has_confirm_button = bool(
			self.invite_template
			and frappe.db.exists(
				"WhatsApp Button", {"parent": self.invite_template, "action_type": "Confirm"}
			)
		)

		if has_confirm_button and self.qr_delivery != "Disabled":
			self.qr_delivery = "On Confirmation"
		elif self.qr_delivery == "On Confirmation":
			self.qr_delivery = "Immediate"
