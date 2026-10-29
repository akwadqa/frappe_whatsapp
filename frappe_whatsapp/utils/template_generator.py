import frappe
import base64
from playwright.sync_api import sync_playwright

def generate_qr_card(template_name: str, context: dict) -> str:
    """Generate a base64-encoded PNG from an HTML template."""
    
    html = frappe.render_template(template_name, context)

    try:
        with sync_playwright() as p:
            with p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]) as browser:
                page = browser.new_page(
                    viewport={"width": 360, "height": 640},
                    device_scale_factor=2
                )
                page.set_content(html, wait_until="networkidle")
                png_bytes = page.screenshot(type="png")

    except Exception:
        frappe.log_error(title="QR Card Generation Failed", message=frappe.get_traceback())
        raise

    if not png_bytes:
        frappe.log_error(title="QR Card Generation Failed", message="No PNG data generated")
        raise 

    return base64.b64encode(png_bytes).decode("utf-8")
