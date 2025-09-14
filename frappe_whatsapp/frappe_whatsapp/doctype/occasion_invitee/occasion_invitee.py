# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from frappe_whatsapp.qr_code import get_qr_code


class OccasionInvitee(Document):
    def validate(self):
    #    self.check_duplicate()
        if self.ticket_id:
            self.qr_raw_data = get_qr_code(self.ticket_id)
    
    def check_duplicate(self):
        exists = frappe.db.exists(
        "Occasion Invitee",
        {"occasion": self.occasion, "invitee": self.invitee}
    )
        if exists and exists != self.name:
            frappe.throw("This invitee is already linked to the selected occasion.")
