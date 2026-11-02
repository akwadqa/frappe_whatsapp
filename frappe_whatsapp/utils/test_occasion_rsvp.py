# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

import base64
from unittest.mock import patch

import frappe
from frappe_whatsapp.testing import IntegrationTestCase

from frappe_whatsapp.utils.occasion_rsvp import update_invitee_rsvp_status
from frappe_whatsapp.utils.webhook import update_message_status

ACCOUNT = "Test WA RSVP Account"
INVITE_TEMPLATE = "rsvp_test_invite-en"
PLAIN_TEMPLATE = "rsvp_test_plain-en"
OUTGOING_ID = "wamid.rsvp_test_out"
ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# The first three labels are not in the hardcoded fallback map, so a match
# proves the action came from WhatsApp Button.action_type. The last one has no
# action_type and relies on the fallback map.
BUTTONS = [
    ("نعم سأحضر", "Confirm"),
    ("لن أحضر", "Decline"),
    ("أين المكان", "Location"),
    ("تأكيد", ""),
]


def fake_qr_card(template_name, context):
    """Distinct bytes per ticket, otherwise File dedupes identical content across tests."""
    png = base64.b64decode(ONE_PIXEL_PNG) + context["qr_image_url"].encode()
    return base64.b64encode(png).decode()


class TestOccasionRsvp(IntegrationTestCase):
    """Guest replies and QR card delivery for Occasion Invitees.

    Everything is created inside the class transaction and rolled back at the end.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_account()
        cls._make_template(INVITE_TEMPLATE, BUTTONS)
        cls._make_template(PLAIN_TEMPLATE, [])
        frappe.get_doc({"doctype": "Occasion Type", "occasion_type": "RSVP Test Type"}).insert(ignore_permissions=True)
        frappe.db.set_single_value("WhatsApp Settings", {
            "confirm_reply_en": "See you there",
            "decline_reply_en": "Sorry to miss you",
            "confirm_reply_ar": "نراك هناك",
            "decline_reply_ar": "نأسف لغيابك",
        })

    @classmethod
    def _make_account(cls):
        frappe.get_doc({
            "doctype": "WhatsApp Account",
            "account_name": ACCOUNT,
            "status": "Active",
            "url": "https://graph.facebook.com",
            "version": "v17.0",
            "phone_id": "rsvp_test_phone_id",
            "business_id": "rsvp_test_business_id",
            "app_id": "rsvp_test_app_id",
            "webhook_verify_token": "rsvp_test_verify_token",
        }).insert(ignore_permissions=True)
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("WhatsApp Account", ACCOUNT, "rsvp_test_token", "token")
        # Only one default may exist, and the real one has no token in this transaction.
        frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_outgoing=0, is_default_incoming=0")
        frappe.db.set_value("WhatsApp Account", ACCOUNT, {"is_default_outgoing": 1, "is_default_incoming": 1})

    @classmethod
    def _make_template(cls, name, buttons):
        """db_insert skips after_insert, which would call Meta."""
        template_name = name.rsplit("-", 1)[0]
        frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "name": name,
            "template_name": template_name,
            "actual_name": template_name,
            "template": "You are invited",
            "category": "UTILITY",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": ACCOUNT,
            "status": "APPROVED",
            "id": f"{template_name}_id",
        }).db_insert()
        for idx, (label, action_type) in enumerate(buttons, start=1):
            frappe.get_doc({
                "doctype": "WhatsApp Button",
                "parent": name,
                "parenttype": "WhatsApp Templates",
                "parentfield": "buttons",
                "idx": idx,
                "button_type": "Quick Reply",
                "button_label": label,
                "action_type": action_type,
            }).db_insert()

    def setUp(self):
        self.post_mock = patch(
            "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.make_post_request",
            return_value={"messages": [{"id": OUTGOING_ID}]},
        ).start()
        self.card_mock = patch(
            "frappe_whatsapp.utils.occasion_rsvp.generate_qr_card", side_effect=fake_qr_card
        ).start()
        self.addCleanup(patch.stopall)
        self.invitees = []

    def tearDown(self):
        # Rollback does not remove files written to disk; File.on_trash does.
        for name in frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Occasion Invitee", "attached_to_name": ["in", self.invitees]},
            pluck="name",
        ):
            frappe.delete_doc("File", name, ignore_permissions=True, force=True)

    # --- fixtures ---

    def make_occasion(self, template=INVITE_TEMPLATE, qr_delivery="On Confirmation", language="English"):
        return frappe.get_doc({
            "doctype": "Occasion",
            "title": "RSVP Test Occasion",
            "type": "RSVP Test Type",
            "invite_template": template,
            "qr_delivery": qr_delivery,
            "language": language,
            "location_name": "Test Hall",
            "location_address": "Test Street 1",
            "map_latitude": "25.2854",
            "map_longitude": "51.5310",
        }).insert(ignore_permissions=True)

    def make_invitee(self, occasion):
        invitee = frappe.get_doc({
            "doctype": "Occasion Invitee",
            "occasion": occasion.name,
            "full_name": "Test Guest",
            "whatsapp_number": "97455500001",
            "party_size": 2,
            "rsvp_status": "Pending",
        }).insert(ignore_permissions=True)
        self.invitees.append(invitee.name)
        return invitee

    def make_invitation(self, invitee, message_id, template=INVITE_TEMPLATE):
        """The outgoing invitation the guest replies to; db_insert avoids sending it."""
        message = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": invitee.whatsapp_number,
            "message_id": message_id,
            "status": "sent",
            "content_type": "text",
            "message_type": "Template",
            "template": template,
            "occasion": invitee.occasion,
            "occasion_invitee": invitee.name,
            "whatsapp_account": ACCOUNT,
        })
        message.flags.ignore_validate = True
        message.db_insert()
        return message

    def invite(self, message_id, **occasion_kwargs):
        occasion = self.make_occasion(**occasion_kwargs)
        invitee = self.make_invitee(occasion)
        self.make_invitation(invitee, message_id)
        return invitee

    def replies(self, invitee, content_type=None):
        filters = {"occasion_invitee": invitee.name, "type": "Outgoing", "message_type": "Manual"}
        if content_type:
            filters["content_type"] = content_type
        return frappe.get_all("WhatsApp Message", filters=filters, fields=["name", "message", "attach", "latitude", "longitude"])

    # --- confirm ---

    def test_confirm_sets_ticket_and_sends_qr_card(self):
        invitee = self.invite("wamid.rsvp_test_confirm")

        update_invitee_rsvp_status("wamid.rsvp_test_confirm", "نعم سأحضر")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Confirmed")
        self.assertEqual(invitee.ticket_id, "wamid.rsvp_test_confirm")
        self.assertTrue(invitee.qr_raw_data)
        self.assertEqual(invitee.replied, 1)
        self.card_mock.assert_called_once()
        cards = self.replies(invitee, "image")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].attach, f"/files/qr-ticket-{invitee.name}.png")
        self.assertEqual(self.replies(invitee, "text"), [])

    def test_confirm_with_qr_disabled_sends_text_only(self):
        invitee = self.invite("wamid.rsvp_test_disabled", qr_delivery="Disabled")

        update_invitee_rsvp_status("wamid.rsvp_test_disabled", "نعم سأحضر")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Confirmed")
        self.assertFalse(invitee.ticket_id)
        self.card_mock.assert_not_called()
        texts = self.replies(invitee, "text")
        self.assertEqual([t.message for t in texts], ["See you there"])
        self.assertEqual(self.replies(invitee, "image"), [])

    def test_confirm_keeps_existing_ticket(self):
        invitee = self.invite("wamid.rsvp_test_keep")
        invitee.db_set("ticket_id", "wamid.rsvp_test_original")

        update_invitee_rsvp_status("wamid.rsvp_test_keep", "نعم سأحضر")

        invitee.reload()
        self.assertEqual(invitee.ticket_id, "wamid.rsvp_test_original")

    # --- decline ---

    def test_decline_sends_language_specific_text(self):
        invitee = self.invite("wamid.rsvp_test_decline", language="Arabic")

        update_invitee_rsvp_status("wamid.rsvp_test_decline", "لن أحضر")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Declined")
        self.assertEqual(invitee.replied, 1)
        self.assertFalse(invitee.ticket_id)
        texts = self.replies(invitee, "text")
        self.assertEqual([t.message for t in texts], ["نأسف لغيابك"])

    # --- location ---

    def test_location_sends_occasion_coordinates(self):
        invitee = self.invite("wamid.rsvp_test_location")

        update_invitee_rsvp_status("wamid.rsvp_test_location", "أين المكان")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Pending")
        self.assertEqual(invitee.replied, 0)
        locations = self.replies(invitee, "location")
        self.assertEqual(len(locations), 1)
        self.assertAlmostEqual(locations[0].latitude, 25.2854)
        self.assertAlmostEqual(locations[0].longitude, 51.5310)

    def test_location_without_coordinates_logs_and_sends_nothing(self):
        invitee = self.invite("wamid.rsvp_test_nocoords")
        frappe.db.set_value("Occasion", invitee.occasion, {"map_latitude": "", "map_longitude": ""})
        errors_before = frappe.db.count("Error Log", {"method": "Missing Location Coordinates"})

        update_invitee_rsvp_status("wamid.rsvp_test_nocoords", "أين المكان")

        self.assertEqual(self.replies(invitee), [])
        self.assertEqual(frappe.db.count("Error Log", {"method": "Missing Location Coordinates"}), errors_before + 1)

    # --- no-ops ---

    def test_fallback_labels_apply_when_button_has_no_action_type(self):
        invitee = self.invite("wamid.rsvp_test_fallback")

        update_invitee_rsvp_status("wamid.rsvp_test_fallback", "تأكيد")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Confirmed")

    def test_unknown_reply_changes_nothing(self):
        invitee = self.invite("wamid.rsvp_test_unknown")

        update_invitee_rsvp_status("wamid.rsvp_test_unknown", "Maybe")
        update_invitee_rsvp_status("wamid.rsvp_test_unknown", ["!=", ""])
        update_invitee_rsvp_status("wamid.rsvp_test_unknown", None)

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Pending")
        self.assertEqual(self.replies(invitee), [])

    def test_unknown_message_id_changes_nothing(self):
        invitee = self.invite("wamid.rsvp_test_known")

        update_invitee_rsvp_status("wamid.rsvp_test_missing", "نعم سأحضر")
        update_invitee_rsvp_status(None, "نعم سأحضر")

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Pending")
        self.assertEqual(self.replies(invitee), [])

    # --- delivery status after a reply ---

    def test_reply_is_not_overwritten_by_later_delivery_status(self):
        confirmed = self.invite("wamid.rsvp_test_confirmed_then_failed")
        declined = self.invite("wamid.rsvp_test_declined_then_failed")
        update_invitee_rsvp_status("wamid.rsvp_test_confirmed_then_failed", "نعم سأحضر")
        update_invitee_rsvp_status("wamid.rsvp_test_declined_then_failed", "لن أحضر")

        update_message_status({"statuses": [
            {"id": "wamid.rsvp_test_confirmed_then_failed", "status": "failed", "errors": [{"code": 131026}]},
            {"id": "wamid.rsvp_test_declined_then_failed", "status": "failed", "errors": [{"code": 131026}]},
        ]})

        confirmed.reload()
        declined.reload()
        self.assertEqual(confirmed.rsvp_status, "Confirmed")
        self.assertEqual(declined.rsvp_status, "Declined")
        self.assertEqual(
            frappe.db.get_value("WhatsApp Message", {"message_id": "wamid.rsvp_test_confirmed_then_failed"}, "status"),
            "failed",
        )

    # --- bulk send ---

    def test_bulk_immediate_delivery_confirms_and_issues_ticket(self):
        occasion = self.make_occasion(template=PLAIN_TEMPLATE, qr_delivery="Immediate")
        invitee = self.make_invitee(occasion)
        bulk = frappe.get_doc({
            "doctype": "Bulk WhatsApp Message",
            "title": "Test RSVP Bulk",
            "recipient_type": "Individual",
            "use_template": 1,
            "template": PLAIN_TEMPLATE,
            "variable_type": "Common",
            "whatsapp_account": ACCOUNT,
            "occasion": occasion.name,
        })
        bulk.append("recipients", {"occasion_invitee": invitee.name, "mobile_number": invitee.whatsapp_number})
        bulk.insert(ignore_permissions=True)

        bulk.create_message_record(bulk.recipients[0])

        invitee.reload()
        self.assertEqual(invitee.rsvp_status, "Confirmed")
        self.assertEqual(invitee.ticket_id, OUTGOING_ID)
        self.assertEqual(invitee.replied, 0)
        self.card_mock.assert_called_once()
        self.assertEqual(len(self.replies(invitee, "image")), 1)
