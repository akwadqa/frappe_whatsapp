# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document

from frappe_whatsapp.qr_code import get_qr_code


class OccasionInvitee(Document):
    def validate(self):
        self.qr_raw_data = get_qr_code(self.name)
