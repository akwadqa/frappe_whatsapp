# Bulk WhatsApp Messaging for Frappe WhatsApp
# bulk_whatsapp_messaging.py

import frappe
from frappe import _
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from frappe.utils import cint, get_datetime, now_datetime
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from frappe_whatsapp.utils import get_whatsapp_account

# Add these files to your frappe_whatsapp app

# 1. First, create a new DocType for Bulk WhatsApp Messaging
# Save this as a Python file in your app's folder: 
# frappe_whatsapp/frappe_whatsapp/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py

BATCH_SIZE = 400
THROTTLE_DELAY = 0.03  # 30ms
DEFAULT_SEND_RATE = 10  # messages per second per batch job
MAX_CONCURRENCY = 50

SENT_STATUSES = ["accepted", "sent", "delivered", "read", "Success"]
FAILED_STATUSES = ["failed", "Failed"]
QUEUED_STATUSES = ["queued", "Queued"]

class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")
    
    def validate(self):
        # self.validate_message()
        self.validate_recipients()
    
    def validate_message(self):
        if not self.message_content:
            frappe.throw(_("Message content is required"))
    
    def validate_recipients(self):
        if not self.recipients and not self.recipient_list:
            frappe.throw(_("At least one recipient or a recipient list is required"))
        
        # If recipient list is provided, count recipients
        if self.recipient_type == 'Recipient List' and self.recipient_list:
            count = frappe.db.count(
                "WhatsApp Recipient",
                {"parent": self.recipient_list}
            )

            if count == 0:
                frappe.throw(_("Selected recipient list has no recipients"))

            self.recipient_count = count
        # If individual recipients are provided
        elif self.recipients:
            self.recipient_count = len(self.recipients)
    
    def on_submit(self):
        # `scheduled_time` is a naive Datetime, interpreted in the site's
        # System Settings timezone - same convention as `now_datetime()`.
        if self.scheduled_time and get_datetime(self.scheduled_time) > now_datetime():
            self.db_set("status", "Scheduled")
        else:
            self.db_set({"status": "Queued", "sent_count": 0})
            self.queue_batches()

    #### Sending Logic ####
    def queue_batches(self):
        recipients = self.get_all_recipients()

        for i in range(0, len(recipients), BATCH_SIZE):
            batch = recipients[i:i + BATCH_SIZE]
            frappe.enqueue_doc(
                self.doctype,
                self.name,
                "process_batch",
                queue="long",
                timeout=900,
                recipients=batch
            )
    
    def get_all_recipients(self):
        if self.recipient_type == 'Recipient List':
            return frappe.get_all(
                "WhatsApp Recipient",
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "recipient_data"]
            )
        else:
            return self.recipients

    def process_batch(self, recipients):
        success = 0
        failed = 0
        
        if self.use_template and self.template:
            success, failed = self.send_concurrently(recipients)
        else:
            for r in recipients:
                try:
                    self.create_message_record(r)
                    success += 1
                except Exception:
                    frappe.log_error("Bulk WhatsApp Batch Error", frappe.get_traceback())
                    failed += 1

                # Make the message visible to status webhooks right away
                frappe.db.commit()  # nosemgrep
                time.sleep(THROTTLE_DELAY)

        frappe.db.sql("""
            UPDATE `tabBulk WhatsApp Message`
            SET sent_count = sent_count + %s
            WHERE name = %s
            """, (success, self.name))
        
        self.update_status()
    
    def send_concurrently(self, recipients):
        """Send template messages with concurrent HTTP calls; DB writes stay in this thread."""
        if self.whatsapp_account:
            account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)
        else:
            account = get_whatsapp_account(account_type="outgoing")
            if not account:
                frappe.throw(_("Please set a default outgoing WhatsApp Account"))
        token = account.get_password("token")

        # Insert every row before sending, so webhooks always find it
        to_send = []
        for r in recipients:
            doc = self.new_message(r)
            doc.whatsapp_account = account.name
            doc.set_new_name()
            try:
                payload = doc.get_template_payload()
                payload["biz_opaque_callback_data"] = doc.name
                to_send.append((doc, payload))
            except Exception as e:
                frappe.log_error("Bulk WhatsApp Batch Error", frappe.get_traceback())
                doc.status = "failed"
                doc.error_message = str(e)
            doc.db_insert()
        frappe.db.commit()  # nosemgrep

        success = 0
        failed = len(recipients) - len(to_send)
        rate = cint(account.get("bulk_send_rate")) or DEFAULT_SEND_RATE
        sender = BulkSender(
            f"{account.url}/{account.version}/{account.phone_id}/messages", token, rate
        )
        pool = ThreadPoolExecutor(max_workers=min(rate, MAX_CONCURRENCY))
        try:
            futures = {pool.submit(sender.post, payload): doc for doc, payload in to_send}
            for future in as_completed(futures):
                if self.save_send_result(futures[future], *future.result()):
                    success += 1
                else:
                    failed += 1
                frappe.db.commit()  # nosemgrep
        finally:
            pool.shutdown(cancel_futures=True)
            sender.close()

        return success, failed

    def save_send_result(self, doc, status_code, response):
        if status_code == 200 and response.get("messages"):
            doc.message_id = response["messages"][0]["id"]
            # A webhook may have already moved the status forward
            frappe.db.sql("""
                UPDATE `tabWhatsApp Message`
                SET message_id = %s,
                    status = CASE WHEN status = 'queued' THEN 'accepted' ELSE status END
                WHERE name = %s
                """, (doc.message_id, doc.name))
            try:
                doc.create_whatsapp_profile()
            except Exception:
                frappe.log_error("Bulk WhatsApp Profile Error", frappe.get_traceback())
            frappe.publish_realtime(
                doc.to,
                {
                    "type": doc.type,
                    "message_id": doc.message_id,
                    "local_message_id": doc.local_message_id,
                    "content": doc.message or doc.attach,
                    "caption": doc.caption,
                },
            )
            return True

        error = response.get("error", {})
        frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Text Message",
            "meta_data": response,
        }).insert(ignore_permissions=True)
        frappe.db.set_value(
            "WhatsApp Message",
            doc.name,
            {
                "status": "failed",
                "error_code": error.get("code"),
                "error_message": error.get("Error", error.get("message")),
            },
            update_modified=False,
        )
        return False

    def create_message_record(self, recipient):
        self.new_message(recipient).insert(ignore_permissions=True)

    def new_message(self, recipient):
        wa_message = frappe.new_doc("WhatsApp Message")
        wa_message.to = recipient.get("mobile_number")
        #wa_message.type = "Outgoing"
        wa_message.message_type = "Text"
        wa_message.status = "queued"
        wa_message.bulk_message_reference = self.name

        if self.whatsapp_account:
            wa_message.whatsapp_account = self.whatsapp_account

        if recipient.get("recipient_data"):
            try:
                wa_message.flags.custom_ref_doc = json.loads(
                    recipient.get("recipient_data", "{}")
                )
            except Exception:
                pass
        
        if self.use_template:
            wa_message.template = self.template
            wa_message.use_template = 1
            wa_message.message_type = "Template"

        mpm_action = self.get_mpm_action_json()
        if mpm_action:
            wa_message.product_catalog_json = json.dumps(mpm_action)
        
        if recipient.get("recipient_data") and self.variable_type == "Unique":
            wa_message.body_param = recipient.get("recipient_data")
        elif self.template_variables and self.variable_type == "Common":
            wa_message.body_param = self.template_variables
        
        if self.attach:
            wa_message.attach = self.attach

        return wa_message

    def update_status(self):
        total = self.recipient_count
        sent = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", SENT_STATUSES],
        })
        failed = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", FAILED_STATUSES],
        })
        queued = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", QUEUED_STATUSES],
        })

        if queued > 0:
            status = "In Progress"
        elif failed > 0 and sent > 0:
            status = "Partially Failed"
        elif failed == total:
            status = "Failed"
        else:
            status = "Completed"

        self.db_set("status", status)
    
    #### RETRY LOGIC ####
    def retry_failed(self):
        failed_messages = frappe.get_all(
            "WhatsApp Message",
            filters={"bulk_message_reference": self.name, "status": ["in", FAILED_STATUSES]},
            fields=["name"],
        )
        for msg in failed_messages:
            frappe.enqueue_doc(
                self.doctype,
                self.name,
                "resend_single_message",
                "long",
                4000,
                message_name=msg.name,
            )
        frappe.msgprint(_("{0} message(s) requeued for sending").format(len(failed_messages)))

    def resend_single_message(self, message_name):
        """Worker entry: re-send a single failed WhatsApp Message."""
        message_doc = frappe.get_doc("WhatsApp Message", message_name)
        # Clear the prior message_id so the template send path (which gates
        # on `not self.message_id`) runs again.
        message_doc.message_id = None
        message_doc.status = "queued"
        message_doc.error_code = None
        message_doc.error_message = None
        message_doc.db_update()
        try:
            message_doc.send_outgoing()
        except Exception:
            message_doc.status = "failed"
            message_doc.db_update()
            frappe.log_error(
                title=f"WhatsApp bulk retry failed: {message_doc.name}"
            )
        
    #### Progress Track ####
    def get_progress(self):
        total = self.recipient_count
        sent = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", SENT_STATUSES]
        })
        failed = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", FAILED_STATUSES]
        })
        queued = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", QUEUED_STATUSES]
        })
        
        return {
            "total": total,
            "sent": sent,
            "failed": failed,
            "queued": queued,
            "percent": (sent / total * 100) if total else 0
        }

    #### MPM ACTION ####
    def get_mpm_action_json(self):
        """Constructs the Meta 'action' JSON by fetching Catalog ID from the Account"""
        if not self.whatsapp_account or not self.thumbnail_product_retailer_id or not self.product_ids:
            return None

        raw_ids = self.product_ids
        # Clean the product list from the user input
        # Convert to a set to remove duplicates, then back to a list
        product_list = list(dict.fromkeys([p.strip() for p in raw_ids.split(",") if p.strip()]))

        if len(product_list) > 30:
            product_list = product_list[:30]
            frappe.msgprint(_("Note: Only the first 30 products were included due to WhatsApp limitations."),
                            indicator="orange")
        return {
            "thumbnail_product_retailer_id": self.thumbnail_product_retailer_id,
            "sections": [
                {
                    "title": self.mpm_header or "Our Products",
                    "product_items": [
                        {"product_retailer_id": pid} for pid in product_list
                    ]
                }
            ]
        }


class BulkSender:
    """Posts payloads to Meta from worker threads at a fixed rate.

    Runs outside the Frappe context: no frappe.* calls in here.
    """

    def __init__(self, url, token, rate):
        self.url = url
        self.headers = {"authorization": f"Bearer {token}"}
        self.interval = 1 / rate
        self.next_at = 0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.sessions = []

    def post(self, payload):
        self.wait()
        try:
            response = self.session().post(self.url, json=payload, headers=self.headers, timeout=30)
        except requests.RequestException as e:
            return None, {"error": {"message": str(e)}}
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"error": {"message": response.text[:500]}}

    def wait(self):
        with self.lock:
            now = time.monotonic()
            send_at = max(now, self.next_at)
            self.next_at = send_at + self.interval
        time.sleep(send_at - now)

    def session(self):
        # One connection per thread, reused across messages
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.sessions.append(self.local.session)
        return self.local.session

    def close(self):
        for session in self.sessions:
            session.close()


def process_scheduled_messages():
    """Scheduler entry (see hooks.py `scheduler_events["all"]`).

    Dispatches submitted Bulk WhatsApp Messages whose `scheduled_time` has
    arrived. Comparison uses `now_datetime()`, which is already resolved to
    the site's System Settings timezone, so `scheduled_time` naturally
    follows whatever timezone is configured there.
    """
    due_messages = frappe.get_all(
        "Bulk WhatsApp Message",
        filters={
            "docstatus": 1,
            "status": "Scheduled",
            "scheduled_time": ["<=", now_datetime()],
        },
        pluck="name",
    )
    for name in due_messages:
        doc = frappe.get_doc("Bulk WhatsApp Message", name)
        doc.db_set({"status": "Queued", "sent_count": 0})
        doc.queue_batches()
