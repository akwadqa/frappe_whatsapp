"""Webhook."""
import frappe
import json
import requests
import time
import base64
import io
import re
from frappe import _
from werkzeug.wrappers import Response
from PIL import Image
import frappe.utils

from frappe_whatsapp.utils import get_whatsapp_account


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
				frappe.get_doc({
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
	"""Update message status."""
	status_data = data['statuses'][0]
	id = status_data['id']
	status = status_data['status']
	conversation = status_data.get('conversation', {}).get('id')
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

	if not name:
		return

	values = {
		"status": status,
		"conversation_id": conversation
	}

	if status == "failed":
		error = (status_data.get("errors") or [{}])[0]
		values["error_code"] = error.get("code")
		values["error_message"] = error.get("title") or error.get("message")

	frappe.db.set_value(
		"WhatsApp Message",
		name,
		values,
		update_modified=False
	)

	# Update Occasion Invitee RSVP status
	occasion_invitee = frappe.db.get_value("WhatsApp Message", name, "occasion_invitee")
	if occasion_invitee and frappe.db.exists("Occasion Invitee", occasion_invitee):
		occ_inv_doc = frappe.get_doc("Occasion Invitee", occasion_invitee)
		if occ_inv_doc.rsvp_status in ["Not Sent", "Failed"] and not occ_inv_doc.ticket_id:
			if status == "sent":
				occ_inv_doc.rsvp_status = "Pending"
			elif status == "failed":
				occ_inv_doc.rsvp_status = "Failed"

			occ_inv_doc.save(ignore_permissions=True)


def update_invitee_rsvp_status(message_id, reply):
	"""Update RSVP status of an Occasion Invitee based on a WhatsApp template quick-reply."""

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
		doc.rsvp_status = new_status if new_status in ["Confirmed", "Declined"] else doc.rsvp_status

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
				"reference_doctype": "Occasion Invitee",
				"reference_name": doc.name
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
			location_name = frappe.db.get_value("Occasion", doc.occasion, "location_name")
			location_address = frappe.db.get_value("Occasion", doc.occasion, "location_address")
			info = extract_google_maps_info(map_link)
			if info.get("latitude") and info.get("longitude"):
				message_data = {
					"doctype": "WhatsApp Message",
					"type": "Outgoing",
					"to": doc.whatsapp_number,
					"occasion_invitee": doc.name,
					"content_type": "location",
					"latitude": info.get("latitude"),
					"longitude": info.get("longitude"),
					"location_name": location_name,
					"location_address": location_address,
					"reference_doctype": "Occasion",
					"reference_name": doc.occasion
				}
				frappe.get_doc(message_data).insert(ignore_permissions=True)
				frappe.db.commit()
			else:
				frappe.log_error(
					title="Missing Location Info",
					message=f"Missing location info for Occasion {doc.occasion}"
				)
			return
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title="RSVP Update Failed",
			message=f"message_id={message_id}, reply={reply}, error={str(e)}"
		)


def upload_base64_png_to_waba(b64_png: str) -> str:
	"""Uploads a PNG to WABA and returns media_id."""
	whatsapp_account = get_whatsapp_account(account_type="outgoing")
	if not whatsapp_account:
		frappe.throw(_("No default outgoing WhatsApp Account configured"))

	token = whatsapp_account.get_password("token")
	url = f"{whatsapp_account.url}/{whatsapp_account.version}/{whatsapp_account.phone_id}/media"

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


def extract_google_maps_info(url):
	"""Extracts latitude, longitude from a Google Maps URL."""

	# Extract lat/lng from !3dLAT!4dLNG pattern (more accurate than @lat,lng)
	coord_match = re.search(r'!3d([-+]?[0-9]*\.?[0-9]+)!4d([-+]?[0-9]*\.?[0-9]+)', url or "")
	if coord_match:
		lat = float(coord_match.group(1))
		lng = float(coord_match.group(2))
	else:
		# fallback to @lat,lng pattern
		at_match = re.search(r'@([-+]?[0-9]*\.?[0-9]+),([-+]?[0-9]*\.?[0-9]+)', url or "")
		if at_match:
			lat = float(at_match.group(1))
			lng = float(at_match.group(2))
		else:
			lat = lng = None

	return {
		"latitude": lat,
		"longitude": lng
	}
