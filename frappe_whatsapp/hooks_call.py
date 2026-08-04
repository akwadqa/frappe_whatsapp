import frappe
import json

def send_survey(doc, method=None):

    if not ( doc.status == "Completed" and doc.has_value_changed("status") ):
        return 

    already_sent = frappe.db.exists("WhatsApp Message", {
        "reference_doctype": "Sales Order",
        "reference_name": doc.name,
        "label": "Post-Order Survey Outgoing"
    })
    if already_sent:
        return
        
    customer_mobile_no = doc.custom_mobile_no
    dflt_outgoing = frappe.db.get_value("WhatsApp Account", {"is_default_outgoing": 1}, "name")
    flow = frappe.db.get_single_value("WhatsApp Settings", "whatsapp_flow_for_completed_orders")

    frappe.get_doc({
        "doctype": "WhatsApp Message",
        "label": "Post-Order Survey Outgoing",
        "type": "Outgoing",
        "to": customer_mobile_no,
        "content_type": "flow",
        "flow": flow,
        "flow_cta":"ابدأ",
        "whatsapp_account": dflt_outgoing,
        "reference_doctype": "Sales Order",
        "reference_name": doc.name,
        "message": "We value your feedback. Would you like to take a moment to fill out our customer-satsifaction survey?",
    }).insert(ignore_permissions=True)
        


        



    
