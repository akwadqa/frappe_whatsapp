# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
# from frappe import _
from frappe.model.document import Document


class Occasion(Document):
	def validate(self):
		self.set_qr_delivery()

	def set_qr_delivery(self):
		#qr_delivery set to "On Confirmation" whenever whatsApp template has a button of action_type "Confirm"
		has_confirm_button = bool(
			self.invite_template
			and frappe.db.exists(
				"WhatsApp Button", {"parent": self.invite_template, "action_type": "Confirm"}
			)
		)

		if has_confirm_button:
			self.qr_delivery = "On Confirmation"
		elif self.qr_delivery == "On Confirmation":
			self.qr_delivery = "Disabled"
