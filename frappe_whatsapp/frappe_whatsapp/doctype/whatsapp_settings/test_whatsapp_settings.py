# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

import frappe
from frappe_whatsapp.testing import IntegrationTestCase


class TestWhatsAppSettings(IntegrationTestCase):
    """Tests for WhatsApp Settings doctype."""

    def test_settings_exists(self):
        """Test WhatsApp Settings singleton exists."""
        settings = frappe.get_single("WhatsApp Settings")
        self.assertIsNotNone(settings)

    def test_settings_is_single(self):
        """Test that WhatsApp Settings is a Single doctype."""
        meta = frappe.get_meta("WhatsApp Settings")
        self.assertTrue(meta.issingle)

    def make_template(self, name, status="APPROVED", header_type="IMAGE"):
        """db_insert skips after_insert, which would call Meta."""
        frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "name": name,
            "template_name": name,
            "template": "Your QR card",
            "category": "UTILITY",
            "language_code": "en",
            "status": status,
            "header_type": header_type,
        }).db_insert()
        return name

    def save_qr_card_template(self, template):
        settings = frappe.get_single("WhatsApp Settings")
        settings.qr_card_template_en = template
        settings.save()

    def test_qr_card_template_accepts_approved_image_template(self):
        template = self.make_template("settings_test_qr_ok")

        self.save_qr_card_template(template)

        self.assertEqual(frappe.db.get_single_value("WhatsApp Settings", "qr_card_template_en"), template)

    def test_qr_card_template_must_be_approved(self):
        template = self.make_template("settings_test_qr_pending", status="PENDING")

        self.assertRaises(frappe.ValidationError, self.save_qr_card_template, template)

    def test_qr_card_template_must_have_image_header(self):
        template = self.make_template("settings_test_qr_text", header_type="TEXT")

        self.assertRaises(frappe.ValidationError, self.save_qr_card_template, template)
