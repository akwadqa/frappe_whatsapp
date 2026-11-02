"""Occasion Invitee RSVP replies and QR card delivery."""
import base64

import frappe

from frappe_whatsapp.utils.template_generator import generate_qr_card

QR_CARD_TEMPLATE = "frappe_whatsapp/templates/QR_Code_template_Kroot.html"
RSVP_ACTIONS = {"Confirm", "Decline", "Location"}

# Used only when the invitation template's buttons carry no action_type.
REPLY_LABEL_ACTIONS = {
	"تأكيد": "Confirm",
	"اعتذار": "Decline",
	"موقع المناسبة": "Location",
	"Confirm": "Confirm",
	"Decline": "Decline",
	"Location": "Location",
}


def update_invitee_rsvp_status(message_id, reply):
	"""Apply a guest's quick-reply button to the invitee linked to the invitation message."""
	invitation = get_invitation(message_id)
	if not invitation:
		return

	action = resolve_reply_action(invitation.template, reply)
	if not action:
		frappe.log_error(
			title="Unrecognized RSVP reply",
			message=f"message_id={message_id}, reply={reply}",
		)
		return

	doc = frappe.get_doc("Occasion Invitee", invitation.occasion_invitee)
	if action == "Location":
		send_location(doc)
		return

	doc.replied = 1
	if action == "Confirm":
		confirm_invitee(doc, ticket_id=message_id)
	else:
		decline_invitee(doc)


def confirm_invitee(doc, ticket_id=None):
	"""Mark the invitee Confirmed and acknowledge with the QR card.

	When the occasion disables QR delivery the acknowledgement is the confirm
	text from WhatsApp Settings and no ticket is issued.
	"""
	doc.rsvp_status = "Confirmed"
	qr_enabled = frappe.db.get_value("Occasion", doc.occasion, "qr_delivery") != "Disabled"
	if qr_enabled and ticket_id and not doc.ticket_id:
		doc.ticket_id = ticket_id
	doc.save(ignore_permissions=True)

	if not qr_enabled:
		send_reply_text(doc, "confirm")
	elif doc.qr_raw_data:
		send_qr_card(doc)
	return doc


def decline_invitee(doc):
	doc.rsvp_status = "Declined"
	doc.save(ignore_permissions=True)
	send_reply_text(doc, "decline")


def get_invitation(message_id):
	"""Return the invitee and template of the outgoing message the guest replied to."""
	if not message_id:
		return None
	invitation = frappe.db.get_value(
		"WhatsApp Message",
		{"message_id": message_id},
		["occasion_invitee", "template"],
		as_dict=True,
	)
	if not invitation or not invitation.occasion_invitee:
		return None
	return invitation


def resolve_reply_action(template, reply):
	"""Prefer the action_type persisted on the template's button; fall back to the known labels."""
	# The webhook is unauthenticated: a non-string reply must not reach a DB filter.
	if not isinstance(reply, str):
		return None
	if template:
		action_type = frappe.db.get_value(
			"WhatsApp Button",
			{"parent": template, "parenttype": "WhatsApp Templates", "button_label": reply},
			"action_type",
		)
		if action_type:
			return action_type if action_type in RSVP_ACTIONS else None
	return REPLY_LABEL_ACTIONS.get(reply)


def send_reply_text(doc, kind):
	language = frappe.db.get_value("Occasion", doc.occasion, "language")
	suffix = "ar" if language == "Arabic" else "en"
	text = (frappe.db.get_single_value("WhatsApp Settings", f"{kind}_reply_{suffix}") or "").strip()
	if text:
		send_outgoing_message(doc, content_type="text", message=text)


def send_location(doc):
	occasion = frappe.db.get_value(
		"Occasion",
		doc.occasion,
		["location_name", "location_address", "map_latitude", "map_longitude"],
		as_dict=True,
	)
	if not occasion.map_latitude or not occasion.map_longitude:
		frappe.log_error(
			title="Missing Location Coordinates",
			message=f"Occasion {doc.occasion} is missing map_latitude/map_longitude",
		)
		return

	send_outgoing_message(
		doc,
		content_type="location",
		latitude=float(occasion.map_latitude),
		longitude=float(occasion.map_longitude),
		location_name=occasion.location_name,
		location_address=occasion.location_address,
		reference_doctype="Occasion",
		reference_name=doc.occasion,
	)


def send_qr_card(doc):
	context = {
		"title": "Personal access card",
		"subtitle": "Please show code to enter",
		"subtitle_ar": "يرجى إبراز الكود للدخول",
		"qr_image_url": doc.qr_raw_data,
		"brand_en": "KROOT",
		"brand_ar": "كروت",
		"guest_count": doc.party_size,
		"website": "www.kroot.com",
	}
	try:
		base64_card = generate_qr_card(QR_CARD_TEMPLATE, context)
	except Exception:
		# generate_qr_card has already written the Error Log; the RSVP itself is kept.
		return

	file_url = _save_qr_card_file(base64_card, doc)
	send_outgoing_message(doc, content_type="image", attach=file_url)


def send_outgoing_message(doc, **fields):
	"""Insert an outgoing WhatsApp Message for the invitee.

	Send failures are logged instead of raised so a Meta error does not undo
	the RSVP update that the webhook request has already made.
	"""
	message = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": doc.whatsapp_number,
		"occasion_invitee": doc.name,
		"occasion": doc.occasion,
		"message_type": "Manual",
		"reference_doctype": "Occasion Invitee",
		"reference_name": doc.name,
		**fields,
	})
	try:
		message.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="RSVP reply failed", message=frappe.get_traceback())


def _save_qr_card_file(base64_png, occasion_invitee_doc):
	"""Save a generated QR ticket card as a public Frappe File and return its URL.

	Meta's Cloud API fetches outgoing images by public link, the same way
	WhatsAppMessage.send_outgoing sends every other image, so no WABA media
	upload is needed.
	"""
	raw = base64.b64decode(base64_png.split(",", 1)[1] if "," in base64_png else base64_png)
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": f"qr-ticket-{occasion_invitee_doc.name}.png",
		"attached_to_doctype": "Occasion Invitee",
		"attached_to_name": occasion_invitee_doc.name,
		"content": raw,
		"is_private": 0,
	}).save(ignore_permissions=True)
	return file_doc.file_url
