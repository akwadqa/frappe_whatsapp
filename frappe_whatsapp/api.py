import frappe
from frappe import _
from frappe.utils import now
from frappe.auth import LoginManager

def gen_response(status_code, error, message, data=None):
    frappe.response["status_code"] = status_code
    frappe.response["error"] = error
    frappe.response["message"] = str(message)
    frappe.response["data"] = data or []
# ================================================================================
@frappe.whitelist(allow_guest=True)
def login(email: str, password: str):
    """
    Login Gate User and return API Key + Secret for token-based auth.
    """

    # Authenticate using Frappe's LoginManager
    login_manager = LoginManager()
    try:
        login_manager.authenticate(email, password)
        login_manager.post_login()
    except Exception as e:
        gen_response(401, 1, _("Invalid email or password") )

    user = frappe.session.user    

    # Ensure API key/secret exists
    api_key = frappe.db.get_value("User", user, "api_key")
    api_secret = frappe.db.get_value("User", user, "api_secret")

    if not api_key or not api_secret:
        # Generate new API key/secret
        user_doc = frappe.get_doc("User", user)
        user_doc.api_key = frappe.generate_hash(length=15)
        user_doc.api_secret = frappe.generate_hash(length=30)
        user_doc.save(ignore_permissions=True)
        api_key, api_secret = user_doc.api_key, user_doc.api_secret

    data = [{
        "user": user,
        "api_key": api_key,
        "api_secret": api_secret
    }]

    gen_response(200, 0, _("Login successful"), data )
# ================================================================================
@frappe.whitelist()
def check_in(qr_code: str, gate: str, checkin_by: str):
    """
    Validate QR token (ticket_id) and log check-in.
    """

    # Validate gate
    gate_doc = frappe.db.get_value("Occasion Gate Checkin", gate, ["name", "active"], as_dict=True)
    if not gate_doc or not gate_doc.active:
        log_checkin(None, gate, qr_code, checkin_by, "Invalid")
        gen_response(400, 1, _("Gate is inactive or not found"))
        return

    # Lookup invitee by ticket_id
    invitee = frappe.db.get_value(
        "Occasion Invitee",
        {"ticket_id": qr_code},
        ["name", "occasion", "rsvp_status", "party_size", "checkin_count"],
        as_dict=True
    )

    if not invitee:
        log_checkin(None, gate, qr_code, checkin_by, "Invalid")
        gen_response(404, 1, _("QR code not recognized"))
        return

    if invitee.rsvp_status != "Confirmed":
        log_checkin(invitee.name, gate, qr_code, checkin_by, "Invalid")
        gen_response(403, 1, _("Invitee not confirmed"))
        return

    try:
        # Lock invitee row for atomic check-in
        row = frappe.db.sql("""
            SELECT name, checkin_count, party_size, occasion
            FROM `tabOccasion Invitee`
            WHERE name=%s
            FOR UPDATE
        """, invitee.name, as_dict=True)[0]

        if row.checkin_count >= row.party_size:
            log_checkin(invitee.name, gate, qr_code, checkin_by, "Duplicate")
            gen_response(409, 1, _("Already checked in"))
            return

        # Increment check-in count
        frappe.db.set_value("Occasion Invitee", invitee.name, {
            "checkin_count": row.checkin_count + 1,
            "last_checkin": now()
        })

        log_checkin(invitee.name, gate, qr_code, checkin_by, "Success")

        data = [{
            "invitee": invitee.name,
            "occasion": invitee.occasion,
            "checked_in": row.checkin_count + 1,
            "party_size": row.party_size
        }]

        frappe.db.commit()
        gen_response(200, 0, _("Check-in successful"), data)

    except Exception as e:
        frappe.db.rollback()
        gen_response(500, 1, _("Internal server error: {0}").format(str(e)))
# ================================================================================
def log_checkin(invitee, gate, qr_code, checkin_by, status):
    frappe.get_doc({
        "doctype": "Occasion Checkin Log",
        "invitee": invitee,
        "gate": gate,
        "qr_code": qr_code,
        "checkin_by": checkin_by,
        "status": status,
        "scan_time": now()
    }).insert(ignore_permissions=True)
# ================================================================================
@frappe.whitelist()
def get_active_gates():    
    gates = frappe.get_all(
        "Occasion Gate Checkin",
        filters={"active": 1},  
        fields=["name", "gate_name", "active"]
    )

    gen_response(200, 0, _("Active Gates fetched successfully"), gates)
# ================================================================================
