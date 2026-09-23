# Bulk WhatsApp Message Sending — Bottleneck Analysis Report

**Date:** 2026-08-26
**Scope:** `frappe_whatsapp` bulk messaging subsystem
**Issue:** Bulk sends to 4,000–50,000 recipients taking 2–12+ hours

---

## 1. Problem Statement

Marketing/operations staff use the `Bulk WhatsApp Message` doctype to send
promotions and reminders to customers. Messages are queued in batches of 400
recipients and processed through Frappe's background job system (`long` queue).

**Observed behavior:**
- A single batch of 4,000 recipients can take 2+ hours
- Larger batches (10,000–50,000) can take 6–12+ hours
- Messages arrive so late that promotions and reminders lose relevance
- Staff cannot predict delivery timing, undermining campaign planning

**Constraints:**
- The fix must not slow down the live site for other users during business hours
- No message duplication or data loss
- Must not exceed WhatsApp/Meta API rate limits (~80 msg/sec per phone number)

---

## 2. Architecture Overview

### 2.1 Message Flow (Current)

```
User submits BulkWhatsAppMessage
  └─ on_submit()                          [bulk_whatsapp_message.py:52]
       └─ queue_batches()                 [bulk_whatsapp_message.py:62]
            └─ Splits recipients into chunks of 400
            └─ Enqueues each chunk:
                 frappe.enqueue_doc(
                     ..., queue="long", timeout=900,
                     method="process_batch", recipients=batch
                 )
                 
                 ┌──────────────────────────────────────────────┐
                 │  process_batch() [ONE worker, sequential]    │
                 │  [bulk_whatsapp_message.py:86]               │
                 │                                              │
                 │  for r in recipients:                        │
                 │    create_message_record(r)                  │
                 │      └─ frappe.new_doc("WhatsApp Message")  │
                 │      └─ .insert()                            │
                 │           └─ before_insert() [HOOK]          │
                 │                └─ send_outgoing()            │
                 │                     └─ send_template()       │
                 │                          └─ notify()         │
                 │                               └─ HTTP POST   │
                 │                                  to Meta API │
                 │    time.sleep(0.03)  ← blocks worker         │
                 └──────────────────────────────────────────────┘
                 
       └─ update_status()                  [bulk_whatsapp_message.py:146]
            └─ 3x frappe.db.count() queries on WhatsApp Message
```

### 2.2 Key Files

| File | Lines | Role |
|------|-------|------|
| `frappe_whatsapp/frappe_whatsapp/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py` | 281 | Batch orchestrator — splits recipients, enqueues batches, processes each batch |
| `frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_message/whatsapp_message.py` | 486 | Core message sender — builds payload, makes HTTP POST to Meta Graph API |
| `frappe_whatsapp/utils/bulk_messaging.py` | 190 | Whitelisted API endpoints — progress tracking, retry, import, status monitor |
| `frappe_whatsapp/frappe_whatsapp/hooks.py` | ~150 | Scheduler events — triggers `process_scheduled_messages` every ~3 minutes |

### 2.3 Key Constants

```python
# bulk_whatsapp_message.py:18-19
BATCH_SIZE = 400        # recipients per batch
THROTTLE_DELAY = 0.03   # 30ms sleep between messages (blocks worker)
```

---

## 3. Code Review — Identified Bottlenecks

### 3.1 CRITICAL: Serial Batch Execution

**File:** `bulk_whatsapp_message.py:67-74`

```python
def queue_batches(self):
    recipients = self.get_all_recipients()
    for i in range(0, len(recipients), BATCH_SIZE):
        batch = recipients[i:i + BATCH_SIZE]
        frappe.enqueue_doc(
            self.doctype, self.name, "process_batch",
            queue="long", timeout=900, recipients=batch
        )
```

**Problem:** All batches are enqueued to `queue="long"`, which defaults to **1 worker**. All batches execute **serially** — batch 2 waits for batch 1 to finish, batch 3 waits for batch 2, etc.

**Impact:**
- 4,000 recipients = 10 batches → all 10 run sequentially
- 10,000 recipients = 25 batches → all 25 run sequentially
- Total time scales linearly with recipient count, with no parallelism

**Fix:** Increase `worker_count` for the `long` queue in Procfile or
`Procfile`. Alternatively, enqueue to a dedicated queue with multiple workers.

---

### 3.2 HIGH: Blocking `time.sleep()` in Worker

**File:** `bulk_whatsapp_message.py:98`

```python
def process_batch(self, recipients):
    success = 0
    failed = 0
    for r in recipients:
        try:
            self.create_message_record(r)
            success += 1
        except Exception:
            frappe.log_error("Bulk WhatsApp Batch Error", frappe.get_traceback())
            failed += 1
        time.sleep(THROTTLE_DELAY)  # ← 30ms dead time per message
```

**Problem:** The sleep blocks the worker thread for 30ms per message.
Across a batch of 400: `400 × 0.03s = 12 seconds of pure dead time`.

**Why it exists:** Originally intended as a rate-limit safeguard to avoid
hitting Meta's API rate limits (~80 msg/sec).

**Why it's unnecessary:** The API call itself (`make_post_request`) takes
200-500ms per message. The natural network latency already throttles throughput
to ~2-5 msg/sec — far below Meta's limit. The sleep is redundant.

**Impact:** 12 seconds wasted per batch. Across 10 batches: 120 seconds.

**Fix:** Remove or reduce to `0.005` (5ms) as a safety margin.

---

### 3.3 HIGH: Redundant Document Loads Per Message

**File:** `whatsapp_message.py:381-384` (inside `notify()`)

```python
def notify(self, data):
    whatsapp_account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)
    token = whatsapp_account.get_password("token")
    # ... HTTP POST ...
```

Every single message loads the full `WhatsApp Account` document from the
database and decrypts the token. For 4,000 messages using the same account:
**4,000 redundant loads**.

**File:** `whatsapp_message.py:212` (inside `send_template()`)

```python
def send_template(self):
    template = frappe.get_doc("WhatsApp Templates", self.template)
    # ... build payload ...
```

For 4,000 template messages: **4,000 redundant template loads**.

**File:** `whatsapp_message.py:242` (dynamic button URL resolution)

```python
ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
url = ref_doc.get_formatted(btn.website_url)
```

If every recipient has a unique dynamic URL button, this loads the reference
document for every message.

**Impact:** ~3-5ms overhead per message from redundant DB roundtrips + doc hydration.
Adds up to ~10-20 seconds per batch.

**Fix:** Cache `WhatsApp Account` and `WhatsApp Templates` at the batch level.
Pass cached data into `create_message_record` instead of re-fetching per message.

---

### 3.4 MODERATE: Heavy Status Counting After Each Batch

**File:** `bulk_whatsapp_message.py:146-170`

```python
def update_status(self):
    total = self.recipient_count
    sent = frappe.db.count("WhatsApp Message", {
        "bulk_message_reference": self.name,
        "status": ["in", ["sent", "delivered", "read", "Success"]],
    })
    failed = frappe.db.count("WhatsApp Message", {
        "bulk_message_reference": self.name,
        "status": "Failed",
    })
    queued = frappe.db.count("WhatsApp Message", {
        "bulk_message_reference": self.name,
        "status": "Queued",
    })
    # ... decide status from counts ...
```

**Problem:** 3 separate `COUNT` queries against the potentially large
`tabWhatsApp Message` table, executed after every batch completes.
The same 3 queries also run in `get_progress()` (UI polling) and
`schedule_bulk_messages()`.

**Impact:** Each `COUNT` on a large table without a covering index can
take 100ms+. Three queries = ~300ms per batch end. Across 10 batches: ~3 seconds.

**More importantly:** This adds unnecessary load on the database server during
bulk processing, which can affect other site users.

**Fix:** Use an atomic counter (increment `sent_count` / `failed_count` on
the `Bulk WhatsApp Message` doc) instead of counting from the child table.
The `sent_count` increment at line 100-104 already does this partially for
successes — extend the pattern to failures.

---

### 3.5 MODERATE: `create_message_record` Triggers Full Lifecycle

**File:** `bulk_whatsapp_message.py:108-144`

```python
def create_message_record(self, recipient):
    wa_message = frappe.new_doc("WhatsApp Message")
    # ... set fields ...
    wa_message.insert(ignore_permissions=True)
```

The `.insert()` call triggers Frappe's full document lifecycle:
- `validate()` → `before_insert()` → DB INSERT → `after_insert()` → `on_update()`

The `before_insert()` hook (`whatsapp_message.py:56`) calls `send_outgoing()`,
which makes the API call. This means the API call is embedded inside the
insert hook — tightly coupling document creation with network I/O.

**Impact:** Every message creation pays the cost of the full Frappe ORM lifecycle
+ a synchronous HTTP call. For 4,000 messages, this is 4,000 full ORM cycles.

**Fix (if needed):** In the bulk context, skip the ORM lifecycle by using
`frappe.db.insert()` with a raw dict, then call `send_outgoing()` separately.
This avoids running `validate`, `on_update`, and other hooks that are
irrelevant for bulk sends.

---

### 3.6 LOW: Dead Code — `schedule_bulk_messages()`

**File:** `bulk_messaging.py:160-190`

```python
@frappe.whitelist()
def schedule_bulk_messages():
    """Background job to process bulk WhatsApp messages"""
    bulk_messages = frappe.get_all(
        "Bulk WhatsApp Message",
        filters={"status": "Queued", "docstatus": 1},
        fields=["name", "recipient_count", "sent_count"]
    )
    for bulk in bulk_messages:
        if cint(bulk.sent_count) >= cint(bulk.recipient_count):
            frappe.db.set_value("Bulk WhatsApp Message", bulk.name, "status", "Completed")
            continue
        failed_count = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": bulk.name, "status": "Failed"
        })
        if cint(bulk.sent_count) - failed_count + cint(failed_count) >= cint(bulk.recipient_count):
            # ... mark Completed or Partially Failed ...
```

**Problem:** This function only checks status and marks bulk messages as
Completed/Partially Failed. It **never triggers any sending**. It's registered
as a hook in `hooks.py` but serves no throughput purpose.

The actual status update already happens in `process_batch()` → `update_status()`.

**Impact:** Minor — runs periodically and adds unnecessary DB queries.

**Fix:** Remove from hooks or consolidate with `update_status()`.

---

### 3.7 LOW: No Retry with Backoff for Failed Messages

**File:** `bulk_whatsapp_message.py:173-207`

```python
def retry_failed(self):
    failed_messages = frappe.get_all(
        "WhatsApp Message",
        filters={"bulk_message_reference": self.name, "status": "Failed"},
        fields=["name"],
    )
    for msg in failed_messages:
        frappe.enqueue_doc(
            self.doctype, self.name, "resend_single_message",
            "long", 4000, message_name=msg.name,
        )
```

**Problem:** Failed messages are only retried manually. There is no automatic
retry with exponential backoff. Each retry is enqueued individually to the
`long` queue (1 worker), so retrying 500 failed messages means 500 sequential
jobs.

**Impact:** Failed messages stay failed until manual intervention. Retry of
many failures is slow.

**Fix:** Add automatic retry with backoff in `process_batch`. Batch failed
messages and retry them in groups, with increasing delays.

---

## 4. Timing Breakdown (Current State)

Assumptions: 4,000 recipients, all template messages, 1 worker on `long` queue.

| Component | Per Message | Per Batch (400) | Total (10 batches) |
|---|---|---|---|
| API call to Meta (HTTP POST) | ~300ms | ~120s | ~1,200s |
| `WhatsApp Account` load + decrypt | ~3ms | ~1.2s | ~12s |
| `WhatsApp Templates` load | ~3ms | ~1.2s | ~12s |
| DB insert (WhatsApp Message) | ~5ms | ~2s | ~20s |
| `time.sleep(0.03)` | 30ms | 12s | ~120s |
| `update_status()` (3x COUNT) | — | ~0.3s | ~3s |
| Profile creation/check | ~2ms | ~0.8s | ~8s |
| **Total** | **~343ms** | **~137s** | **~1,375s (~23 min)** |

At 10,000 recipients (25 batches): **~57 minutes**
At 50,000 recipients (125 batches): **~4.7 hours**

---

## 5. Parts of Code to Modify

### 5.1 Must Change

| File | Lines | Change |
|---|---|---|
| `bulk_whatsapp_message.py` | 18-19 | Make `BATCH_SIZE` and `THROTTLE_DELAY` configurable or remove delay |
| `bulk_whatsapp_message.py` | 86-106 | Remove `time.sleep()`, add failure counting, cache template/account |
| `bulk_whatsapp_message.py` | 108-144 | Pass cached account/template data into `create_message_record` |
| `bulk_whatsapp_message.py` | 146-170 | Replace 3x `COUNT` with atomic counter updates |
| Server config (Procfile / supervisor) | — | Increase `worker_count` for `long` queue to 4-8 |

### 5.2 Should Consider Changing

| File | Lines | Change |
|---|---|---|
| `whatsapp_message.py` | 381-384 | Accept pre-loaded `whatsapp_account` + `token` as parameter in `notify()` |
| `whatsapp_message.py` | 212 | Accept pre-loaded template as parameter in `send_template()` |
| `bulk_messaging.py` | 160-190 | Remove or consolidate `schedule_bulk_messages()` |
| `bulk_whatsapp_message.py` | 173-207 | Add automatic retry with backoff for failed messages |

### 5.3 Do NOT Change

| File | Reason |
|---|---|
| `whatsapp_message.py:56` (`before_insert`) | Used by single sends, notifications, webhooks — changing this breaks all other message paths |
| `whatsapp_message.py:66` (`send_outgoing`) | Same — shared by all outgoing message types |
| `whatsapp_message.py:379` (`notify`) | Core API caller — changes here affect every message type |
| `hooks.py` scheduler events | Notification triggers are independent of bulk sending |

---

## 6. Recommended Fix Strategy

### Phase 1: Quick Wins (Expected: 3-5x improvement)

1. **Remove `time.sleep(0.03)`** — eliminates 12s per batch dead time
2. **Increase `long` queue workers to 4** — enables parallel batch processing
3. **Cache `WhatsApp Account` at batch level** — avoids 4,000 redundant doc loads

**Projected timing for 4,000 recipients:** ~23 min → **~5-7 min**

### Phase 2: Optimization (Expected: additional 2x improvement)

4. **Cache `WhatsApp Templates` at batch level** — avoids 4,000 redundant template loads
5. **Replace COUNT queries with atomic counters** — reduces DB load
6. **Batch retry for failed messages** — faster recovery from failures

**Projected timing for 4,000 recipients:** ~5-7 min → **~3-4 min**

### Phase 3: Architecture (If needed for 50,000+ recipients)

7. **Dedicated queue with controlled concurrency** — separate bulk sending from other long jobs
8. **Rate-limit-aware batching** — dynamically adjust batch timing based on Meta API response headers
9. **Skip ORM lifecycle for bulk** — use raw `frappe.db.insert()` to avoid unnecessary hooks

---

## 7. Risk Assessment

| Change | Risk | Mitigation |
|---|---|---|
| Removing sleep | Low — API latency already throttles to ~3 msg/sec | Monitor Meta API response codes for 429 |
| Increasing workers | Medium — more parallel API streams | Cap at 4-8 workers; Meta allows ~80 msg/sec, 8 workers × 3 msg/sec = 24 msg/sec (safe) |
| Caching templates/accounts | Low — data doesn't change during batch | Invalidate cache if `on_update` fires during batch (unlikely) |
| Atomic counters | Low — simpler than COUNT queries | Ensure `sent_count` + `failed_count` + `queued_count` always sum to `recipient_count` |
| Raw DB insert for bulk | Medium — skips validation | Only use for bulk path; keep ORM for single sends |

---

## 8. Files Reference

All paths relative to `/home/mohammad/akwad/frappe-dev/akwad_bench/apps/frappe_whatsapp/`:

```
frappe_whatsapp/frappe_whatsapp/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py  (281 lines)
frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_message/whatsapp_message.py            (486 lines)
frappe_whatsapp/utils/bulk_messaging.py                                                  (190 lines)
frappe_whatsapp/frappe_whatsapp/hooks.py                                                 (~150 lines)
frappe_whatsapp/utils/__init__.py                                                        (~210 lines)
```
