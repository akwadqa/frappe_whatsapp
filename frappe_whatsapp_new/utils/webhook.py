"""Webhook."""
import base64
import json

import frappe
import requests
from frappe import _
from werkzeug.wrappers import Response
import frappe.utils

from frappe_whatsapp_new.utils import get_whatsapp_account
from frappe_whatsapp_new.utils.template_generator import generate_qr_card


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")
	webhook_verify_token = frappe.db.get_value(
		'WhatsApp Account',
		{"webhook_verify_token": verify_token},
		'webhook_verify_token'
	)
	if not webhook_verify_token:
		frappe.throw("No matching WhatsApp account")

	if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post():
	"""Post."""
	data = frappe.local.form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": frappe.as_json(data)
	}).insert(ignore_permissions=True)

	messages = []
	phone_id = None
	try:
		messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
		phone_id = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("metadata", {}).get("phone_number_id")
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

	whatsapp_account = get_whatsapp_account(phone_id) if phone_id else None

	# Only `messages` events carry `metadata.phone_number_id`. Status-change
	# events (`message_template_status_update`, message status callbacks) have
	# no metadata, so `phone_id` is None and `whatsapp_account` is also None
	# for them by design. Gating the entire handler on `whatsapp_account`
	# silently drops every template-status update; gate only the message-
	# ingestion branch instead.
	if messages and not whatsapp_account:
		return

	if messages:
		for message in messages:
			if frappe.db.exists("WhatsApp Message", {"message_id": message['id']}):
				continue
			message_type = message['type']
			is_reply = True if message.get('context') and 'forwarded' not in message.get('context') else False
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
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
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
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				interactive_data = message['interactive']
				interactive_type = interactive_data.get('type')

				# Handle button reply
				if interactive_type == 'button_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['button_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle list reply
				elif interactive_type == 'list_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['list_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle WhatsApp Flows (nfm_reply)
				elif interactive_type == 'nfm_reply':
					nfm_reply = interactive_data['nfm_reply']
					response_json_str = nfm_reply.get('response_json', '{}')

					# Parse the response JSON
					try:
						flow_response = json.loads(response_json_str)
					except json.JSONDecodeError:
						flow_response = {}

					# Create a summary message from the flow response
					summary_parts = []
					for key, value in flow_response.items():
						if value:
							summary_parts.append(f"{key}: {value}")
					summary_message = ", ".join(summary_parts) if summary_parts else "Flow completed"

					msg_doc = frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": summary_message,
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "flow",
						"flow_response": json.dumps(flow_response),
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)

					# Publish realtime event for flow response
					frappe.publish_realtime(  # nosemgrep: frappe-realtime-pick-room -- intentional site-wide fan-out for chat UIs (whatsapp_chat companion app) listening for inbound flow responses
						"whatsapp_flow_response",
						{
							"phone": message['from'],
							"message_id": message['id'],
							"flow_response": flow_response,
							"whatsapp_account": whatsapp_account.name
						}
					)
			# NEW: Handle Shopping Cart / Orders from MPM
			elif message_type == 'order':
				order_data = message['order']

				# Inject the raw data into product_catalog_json
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": _("New Order Received via WhatsApp"),
					"message_id": message['id'],
					"content_type": "order",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
					"product_catalog_json": json.dumps(order_data)
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "sticker", "audio", "video", "document"]:
				token = whatsapp_account.get_password("token")
				url = f"{whatsapp_account.url}/{whatsapp_account.version}/"

				media_id = message[message_type]["id"]
				file_name = message.get(message_type).get("filename")
				caption = message.get(message_type).get("caption")

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
						file_name = message.get(message_type, {}).get("filename")
						if not file_name:
							file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": f"/files/{file_name}",
							"content_type" : message_type,
							"profile_name":sender_profile_name,
							"caption": caption,
							"whatsapp_account":whatsapp_account.name
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
				msg_doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
				# Invite/RSVP flow: route the guest's quick-reply button text to
				# the linked Occasion Invitee. TODO: once the Templates API grows
				# per-button `linked_template` support, resolve the action from
				# the matched button/template instead of this hardcoded label map.
				update_invitee_rsvp_status(reply_to_message_id, message['button']['text'])
			else:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": message[message_type].get(message_type),
					"content_type" : message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
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
	"""Update message status + sync Occasion Invitee RSVP status."""
	try:
		statuses = (data or {}).get("statuses") or []
		if not statuses:
			return

		st = statuses[0] or {}
		msg_id = st.get("id")
		status = (st.get("status") or "").lower()
		conversation = (st.get("conversation") or {}).get("id")

		if not msg_id or not status:
			return

		name = frappe.db.get_value("WhatsApp Message", {"message_id": msg_id}, "name")
		if not name:
			return

		msg_doc = frappe.get_doc("WhatsApp Message", name)
		msg_doc.status = status
		frappe.db.set_value(
			"WhatsApp Message",
			name,
			{
				"status": status,
				"conversation_id": conversation
			},
			update_modified=False
		)

		# Invite/RSVP flow: reflect delivery failures back on the invitee so
		# retries/resends can target them (not part of develop's generic
		# message-status handling, which has no notion of Occasion Invitee).
		SUCCESS_STATES = {"sent", "delivered", "read"}
		FAIL_STATES = {"failed", "undelivered"}

		if msg_doc.occasion_invitee and frappe.db.exists("Occasion Invitee", msg_doc.occasion_invitee):
			inv = frappe.get_doc("Occasion Invitee", msg_doc.occasion_invitee)

			if inv.rsvp_status not in ["Confirmed", "Declined"] and not (inv.replied or 0):
				if status in SUCCESS_STATES:
					if inv.rsvp_status in ["Not Sent", "Failed"]:
						inv.db_set("rsvp_status", "Pending", update_modified=False)

				elif status in FAIL_STATES:
					if inv.rsvp_status in ["Not Sent", "Pending"]:
						inv.db_set("rsvp_status", "Failed", update_modified=False)

		frappe.db.commit()

	except Exception:
		frappe.log_error("error in updating message status", frappe.get_traceback())


def update_invitee_rsvp_status(message_id, reply):
	"""Update RSVP status of an Occasion Invitee based on WhatsApp reply.

	Outgoing replies are created as plain `WhatsApp Message` docs and left to
	the doctype's own `before_insert`/`send_outgoing` to resolve the default
	WhatsApp Account and dispatch to Meta - no manual credential handling
	needed here (develop's `WhatsAppMessage.set_whatsapp_account()` already
	covers that requirement).
	"""
	try:
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

		# Mapping to allow translation and different templates.
		status_map = {
			"تأكيد": "Confirmed",
			"اعتذار": "Declined",
			"موقع المناسبة": "Location",
			"Confirm": "Confirmed",
			"Decline": "Declined",
			"Location": "Location",
		}
		reply_r = status_map.get(reply)
		allowed = {"Confirmed", "Declined", "Location"}
		new_status = reply_r.strip() if reply_r else None

		if new_status not in allowed:
			frappe.log_error(
				title="Unrecognized reply",
				message=f"Unrecognized reply: {reply}"
			)
			return

		doc = frappe.get_doc("Occasion Invitee", occasion_invitee)
		doc.rsvp_status = new_status if new_status in ["Confirmed", "Declined"] else doc.rsvp_status

		# Check if QR code is required and generate ticket_id
		requires_qr_code = frappe.db.get_value("Occasion", doc.occasion, "requires_qr_code")
		if requires_qr_code and new_status == "Confirmed" and not doc.ticket_id:
			doc.ticket_id = message_id

		doc.save(ignore_permissions=True)
		frappe.db.commit()

		settings = frappe.get_single("WhatsApp Settings")
		language = frappe.db.get_value("Occasion", doc.occasion, "language")
		confirm_text = decline_text = None
		if language == "Arabic":
			confirm_text = (settings.get("confirm_reply_ar") or "").strip()
			decline_text = (settings.get("decline_reply_ar") or "").strip()
		elif language == "English":
			confirm_text = (settings.get("confirm_reply_en") or "").strip()
			decline_text = (settings.get("decline_reply_en") or "").strip()

		def send_text_message(text):
			frappe.get_doc({
				"doctype": "WhatsApp Message",
				"type": "Outgoing",
				"to": doc.whatsapp_number,
				"occasion_invitee": doc.name,
				"occasion": doc.occasion,
				"content_type": "text",
				"message_type": "Manual",
				"message": text,
				"reference_doctype": "Occasion Invitee",
				"reference_name": doc.name
			}).insert(ignore_permissions=True)

		if new_status == "Confirmed":
			try:
				if doc.qr_raw_data:
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

					base64_card = generate_qr_card(
						"frappe_whatsapp_new/templates/QR_Code_template_Kroot.html", context
					)
					file_url = _save_qr_card_file(base64_card, doc)

					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Outgoing",
						"to": doc.whatsapp_number,
						"occasion_invitee": doc.name,
						"occasion": doc.occasion,
						"message_type": "Manual",
						"reference_doctype": "Occasion Invitee",
						"reference_name": doc.name,
						"content_type": "image",
						"attach": file_url
					}).insert(ignore_permissions=True)

					doc.replied = 1
					doc.save(ignore_permissions=True)
					frappe.db.commit()

			except Exception as e:
				frappe.log_error("error in sending qr image", str(e))

		elif new_status == "Declined":
			try:
				if decline_text:
					send_text_message(decline_text)
				doc.replied = 1
				doc.save(ignore_permissions=True)
				frappe.db.commit()
			except Exception as e:
				frappe.log_error("error in sending decline message", str(e))

		elif new_status == "Location":
			location_name = frappe.db.get_value("Occasion", doc.occasion, "location_name")
			location_address = frappe.db.get_value("Occasion", doc.occasion, "location_address")
			lat = frappe.db.get_value("Occasion", doc.occasion, "map_latitude")
			lng = frappe.db.get_value("Occasion", doc.occasion, "map_longitude")

			if lat is None or lng is None:
				frappe.log_error(
					title="Missing Location Coordinates",
					message=f"Occasion {doc.occasion} is missing map_latitude/map_longitude"
				)
				return

			try:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Outgoing",
					"to": doc.whatsapp_number,
					"occasion_invitee": doc.name,
					"occasion": doc.occasion,
					"content_type": "location",
					"latitude": float(lat),
					"longitude": float(lng),
					"location_name": location_name,
					"location_address": location_address,
					"reference_doctype": "Occasion",
					"reference_name": doc.occasion
				}).insert(ignore_permissions=True)
				frappe.db.commit()
			except Exception as e:
				frappe.log_error("send location error", str(e))

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title="RSVP Update Failed",
			message=f"message_id={message_id}, reply={reply}, error={str(e)}"
		)


def _save_qr_card_file(base64_png, occasion_invitee_doc):
	"""Save a generated QR ticket card as a public Frappe File and return its URL.

	Meta's Cloud API can fetch outgoing images by public link (the same
	mechanism develop's WhatsAppMessage.send_outgoing already uses for every
	other image), so the ticket card is saved as a File instead of uploaded
	to Meta's media endpoint - no WABA media_id round-trip needed.
	"""
	raw = base64.b64decode(base64_png.split(",", 1)[1] if "," in base64_png else base64_png)
	file_name = f"qr-ticket-{occasion_invitee_doc.name}.png"
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"attached_to_doctype": "Occasion Invitee",
		"attached_to_name": occasion_invitee_doc.name,
		"content": raw,
		"is_private": 0
	}).save(ignore_permissions=True)
	return file_doc.file_url
