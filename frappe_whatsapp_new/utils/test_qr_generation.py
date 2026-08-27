import frappe
from frappe_whatsapp_new.utils.qr_code import get_qr_code
from frappe_whatsapp_new.utils.template_generator import generate_qr_card

def test_qr_card_generation():
    # Generate QR code
    qr_data = "INV-2023-001"
    qr_image_url = get_qr_code(qr_data)

    # Template context
    context = {
        "title": "Personal access card",
        "subtitle": "Please show code to enter",
        "qr_image_url": qr_image_url,
        "brand_en": "KROOT",
        "brand_ar": "كروت",
        "guest_count": 1,
        "website": "www.kroot.com",
    }

    # Generate card
    card = generate_qr_card("frappe_whatsapp_new/templates/QR_Code_template_Kroot.html", context)

    return f"PNG generated: {card}"


if __name__ == "__main__":
    test_qr_card_generation()
