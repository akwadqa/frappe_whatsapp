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
    
    template = frappe.db.get_single_value("WhatsApp Settings", "whatsapp_template_for_completed_orders")
    if template == None:
        return

    frappe.get_doc({
        "doctype": "WhatsApp Message",
        "label": "Post-Order Survey Outgoing",
        "type": "Outgoing",
        "to": customer_mobile_no,
        "content_type": "text",
        "use_template": True,
        "template": template, 
        "whatsapp_account": dflt_outgoing,
        "reference_doctype": "Sales Order",
        "reference_name": doc.name,
    }).insert(ignore_permissions=True)
        


        



    
