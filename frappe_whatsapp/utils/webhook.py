"""Webhook."""
import frappe
import json
import requests
import time
from werkzeug.wrappers import Response
import frappe.utils
import base64, io, json, requests
from PIL import Image


@frappe.whitelist(allow_guest=True)
def webhook():
    """Meta webhook."""
    if frappe.request.method == "GET":
        return get()
    return post()


def get():
    """Get."""
    hub_challenge = frappe.form_dict.get("hub.challenge")
    webhook_verify_token = frappe.db.get_single_value(
        "WhatsApp Settings", "webhook_verify_token"
    )

    if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
        frappe.throw("Verify token does not match")

    return Response(hub_challenge, status=200)

def post():
    """Post."""
    data = frappe.local.form_dict
    frappe.get_doc({
        "doctype": "WhatsApp Notification Log",
        "template": "Webhook",
        "meta_data": json.dumps(data)
    }).insert(ignore_permissions=True)

    messages = []
    try:
        messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
    except KeyError:
        messages = data["entry"]["changes"][0]["value"].get("messages", [])
    sender_profile_name = next(
        (
            contact.get("profile", {}).get("name")
            for entry in data.get("entry", [])
            for change in entry.get("changes", [])
            for contact in change.get("value", {}).get("contacts", [])
        ),
        None,
    )


    if messages:
        for message in messages:
            message_id = message['id']
            if frappe.db.exists("WhatsApp Message", {"message_id": message_id}):
                return
            message_type = message['type']
            is_reply = True if message.get('context') else False
            reply_to_message_id = message['context']['id'] if is_reply else None
            if message_type == 'text':
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Incoming",
                    "from": message['from'],
                    "message": message['text']['body'],
                    "message_id": message['id'],
                    "reply_to_message_id": reply_to_message_id,
                    "is_reply": is_reply,
                    "content_type":message_type,
                    "profile_name":sender_profile_name
                }).insert(ignore_permissions=True)
            elif message_type == 'reaction':
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Incoming",
                    "from": message['from'],
                    "message": message['reaction']['emoji'],
                    "reply_to_message_id": message['reaction']['message_id'],
                    "message_id": message['id'],
                    "content_type": "reaction",
                    "profile_name":sender_profile_name
                }).insert(ignore_permissions=True)
            elif message_type == 'interactive':
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Incoming",
                    "from": message['from'],
                    "message": message['interactive']['nfm_reply']['response_json'],
                    "message_id": message['id'],
                    "content_type": "flow",
                    "profile_name":sender_profile_name
                }).insert(ignore_permissions=True)
            elif message_type in ["image", "audio", "video", "document"]:
                settings = frappe.get_doc(
                            "WhatsApp Settings", "WhatsApp Settings",
                        )
                token = settings.get_password("token")
                url = f"{settings.url}/{settings.version}/"


                media_id = message[message_type]["id"]
                headers = {
                    'Authorization': 'Bearer ' + token

                }
                response = requests.get(f'{url}{media_id}/', headers=headers)

                if response.status_code == 200:
                    media_data = response.json()
                    media_url = media_data.get("url")
                    mime_type = media_data.get("mime_type")
                    file_extension = mime_type.split('/')[1]

                    media_response = requests.get(media_url, headers=headers)
                    if media_response.status_code == 200:

                        file_data = media_response.content
                        file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

                        message_doc = frappe.get_doc({
                            "doctype": "WhatsApp Message",
                            "type": "Incoming",
                            "from": message['from'],
                            "message_id": message['id'],
                            "reply_to_message_id": reply_to_message_id,
                            "is_reply": is_reply,
                            "message": message[message_type].get("caption",f"/files/{file_name}"),
                            "content_type" : message_type,
                            "profile_name":sender_profile_name
                        }).insert(ignore_permissions=True)

                        file = frappe.get_doc(
                            {
                                "doctype": "File",
                                "file_name": file_name,
                                "attached_to_doctype": "WhatsApp Message",
                                "attached_to_name": message_doc.name,
                                "content": file_data,
                                "attached_to_field": "attach"
                            }
                        ).save(ignore_permissions=True)


                        message_doc.attach = file.file_url
                        message_doc.save()
            elif message_type == "button":                
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Incoming",
                    "from": message['from'],
                    "message": message['button']['text'],
                    "message_id": message['id'],
                    "reply_to_message_id": reply_to_message_id,
                    "is_reply": is_reply,
                    "content_type": message_type,
                    "profile_name":sender_profile_name
                }).insert(ignore_permissions=True)
                update_invitee_rsvp_status(reply_to_message_id, message['button']['text'])
            else:
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Incoming",
                    "from": message['from'],
                    "message_id": message['id'],
                    "message": message[message_type].get(message_type),
                    "content_type" : message_type,
                    "profile_name":sender_profile_name
                }).insert(ignore_permissions=True)

    else:
        changes = None
        try:
            changes = data["entry"][0]["changes"][0]
        except KeyError:
            changes = data["entry"]["changes"][0]
        update_status(changes)
    return

def update_status(data):
    """Update status hook."""
    if data.get("field") == "message_template_status_update":
        update_template_status(data['value'])

    elif data.get("field") == "messages":
        update_message_status(data['value'])

def update_template_status(data):
    """Update template status."""
    frappe.db.sql(
        """UPDATE `tabWhatsApp Templates`
        SET status = %(event)s
        WHERE id = %(message_template_id)s""",
        data
    )

def update_message_status(data):
    """Update message status."""
    id = data['statuses'][0]['id']
    status = data['statuses'][0]['status']
    conversation = data['statuses'][0].get('conversation', {}).get('id')
    name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

    doc = frappe.get_doc("WhatsApp Message", name)
    doc.status = status
    if conversation:
        doc.conversation_id = conversation
    doc.save(ignore_permissions=True)

def update_invitee_rsvp_status(message_id, reply):
    """Update RSVP status of an Occasion Invitee based on WhatsApp reply."""    
    
    if not message_id:
        frappe.log_error(
            title="Missing message_id",
            message="update_invitee_rsvp_status was called without a message_id"
        )
        return

    occasion_invitee = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": message_id},
        fieldname="occasion_invitee"
    )
    if not occasion_invitee:
        frappe.log_error(
            title="No invitee found",
            message=f"No invitee found for message_id={message_id}"
        )
        return

    status_map = {
        "تأكيد": "Confirmed",
        "اعتذار": "Declined",
        "موقع المناسبة": "Location"
    }
    new_status = status_map.get(reply)
    if not new_status:
        frappe.log_error(
            title="Unrecognized reply",
            message=f"Unrecognized reply: {reply}"
        )
        return

    doc = frappe.get_doc("Occasion Invitee", occasion_invitee)
    doc.rsvp_status = new_status if new_status in ["Confirmed" , "Declined"] else doc.rsvp_status

    # Check if QR code is required and generate ticket_id
    requires_qr_code = frappe.db.get_value("Occasion", doc.occasion, "requires_qr_code")
    if requires_qr_code and new_status == "Confirmed" and not doc.ticket_id:
        doc.ticket_id = message_id

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    # Handle sending response messages
    def send_whatsapp_message(template, extra_fields=None):
        """Helper to create outgoing WhatsApp message"""
        message_data = {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": doc.whatsapp_number,
            "occasion_invitee": doc.name,
            "message_type": "Template",
            "use_template": 1,
            "template": template,
            "reference_doctype": "Occasion",
            "reference_name": doc.occasion
        }
        if extra_fields:
            message_data.update(extra_fields)
        frappe.get_doc(message_data).insert(ignore_permissions=True)

    if new_status == "Confirmed":
        confirmed_template = frappe.db.get_value("Occasion", doc.occasion, "confirmed_template")
        if confirmed_template:
            if doc.qr_raw_data:
                # Upload QR code to WABA and send with media_id
                doc.media_id = upload_base64_png_to_waba(doc.qr_raw_data)
                send_whatsapp_message(confirmed_template, {
                    "content_type": "image",
                    "media_id": doc.media_id,
                })
            else:
                # Send template without image
                send_whatsapp_message(confirmed_template)

            doc.replied = 1
            doc.save(ignore_permissions=True)
            frappe.db.commit()

    elif new_status == "Declined":
        declined_template = frappe.db.get_value("Occasion", doc.occasion, "declined_template")
        if declined_template:
            send_whatsapp_message(declined_template)
            doc.replied = 1
            doc.save(ignore_permissions=True)
            frappe.db.commit()
    elif new_status == "Location":
        map_link = frappe.db.get_value("Occasion", doc.occasion, "map_link")
        if map_link:
            message_data = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "to": doc.whatsapp_number,
                "occasion_invitee": doc.name,
                "message": map_link,
                "reference_doctype": "Occasion",
                "reference_name": doc.occasion
            }
            frappe.get_doc(message_data).insert(ignore_permissions=True)
            frappe.db.commit()


def upload_base64_png_to_waba(b64_png: str) -> str:
    """Uploads a PNG to WABA and returns media_id."""
    settings = frappe.get_doc(
            "WhatsApp Settings",
            "WhatsApp Settings",
        )
    token = settings.get_password("token")

    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }    
    
    url = f"{settings.url}/{settings.version}/{settings.phone_id}/media"

    png_bytes = normalize_png(b64_png)
    files = {"file": ("qr.png", io.BytesIO(png_bytes), "image/png")}
    data = {"messaging_product": "whatsapp"}
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]

def normalize_png(b64_png: str) -> bytes:
    """Ensure PNG is RGB 8-bit and return clean binary."""
    raw = base64.b64decode(b64_png.split(",", 1)[1] if "," in b64_png else b64_png)
    im = Image.open(io.BytesIO(raw))

    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")

    buf = io.BytesIO()
    im.save(buf, format="PNG")   # Pillow will default to 8-bit RGB/ RGBA
    return buf.getvalue()
        
        
