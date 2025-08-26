# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class WhatsAppNotificationLog(Document):
    @staticmethod
    def clear_old_logs(days=3):
        from frappe.query_builder import Interval
        from frappe.query_builder.functions import Now

        table = frappe.qb.DocType("WhatsApp Notification Log")
        frappe.db.delete(table, filters=(table.modified < (Now() - Interval(days=days))))
