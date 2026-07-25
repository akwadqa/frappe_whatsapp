frappe.ui.form.on("WhatsApp Recipient List", {
  refresh: function (frm) {
    frm.fields_dict.import_button.onclick = function () {
      if (!frm.doc.doctype_to_import || !frm.doc.mobile_field) {
        frappe.throw(
          __("Please select a DocType and Mobile Field before importing")
        );
        return;
      }

      let filters = null;
      if (frm.doc.import_filters) {
        try {
          filters = JSON.parse(frm.doc.import_filters);
        } catch (e) {
          frappe.throw(__("Invalid JSON in Filters field"));
          return;
        }
      }

      frappe.call({
        method: "frappe_whatsapp.utils.bulk_messaging.import_recipients",
        args: {
          list_name: frm.doc.name,
          doctype: frm.doc.doctype_to_import,
          mobile_field: frm.doc.mobile_field,
          name_field: frm.doc.name_field,
          filters: filters,
          limit: frm.doc.import_limit,
          data_fields: frm.doc.data_fields,
        },
        callback: function (r) {
          if (r.message) {
            frappe.msgprint(
              __(`${r.message} recipients imported successfully`)
            );
            frm.reload_doc();
          }
        },
      });
    };

    frm.fields_dict.import_customers_button.onclick = function () {
      const recipients_type = frm.doc.select_recipients_type;

      if (recipients_type === "Inactive Customers") {
        if (
          !frm.doc.days_since_last_order ||
          frm.doc.days_since_last_order <= 0
        ) {
          frappe.msgprint(
            __("Please enter a positive number for Days Since Last Order.")
          );
          return;
        }
      }

      if (recipients_type === "Recently Registered Customers") {
        if (
          !frm.doc.registered_in_the_last_days ||
          frm.doc.registered_in_the_last_days <= 0
        ) {
          frappe.msgprint(
            __(
              "Please enter a positive number for Registered In The Last Days."
            )
          );
          return;
        }
      }

      frappe.call({
        method: "frappe_whatsapp.utils.bulk_messaging.get_customers_for_import",
        args: {
          recipients_type: frm.doc.select_recipients_type,
          days_since_last_order: frm.doc.days_since_last_order,
          registered_in_the_last_days: frm.doc.registered_in_the_last_days,
        },
        freeze: true,
        freeze_message: __("Importing Customers..."),
        callback: function (r) {
          if (r.message) {
            frm.clear_table("recipients");
            r.message.forEach(function (c) {
              frm.add_child("recipients", c);
            });
            frm.refresh_field("recipients");
            frappe.msgprint(
              __(`${r.message.length} recipients imported successfully`)
            );
          }
        },
        error: function (err) {
          console.error(err);
          frappe.msgprint(__("An error occurred. Check the server logs."));
        },
      });
    };

    // Add a button to validate all recipients
    frm.add_custom_button(__("Validate Recipients"), function () {
      let invalid = [];

      (frm.doc.recipients || []).forEach(function (row, idx) {
        let mobile = row.mobile_number || "";

        // Remove non-numeric characters except '+'
        mobile = mobile.replace(/[^\d+]/g, "");

        // Basic validation - should start with + or number and be at least 10 digits
        if (
          !/^(\+|[0-9])/.test(mobile) ||
          mobile.replace(/\+/g, "").length < 10
        ) {
          invalid.push({
            idx: idx + 1,
            mobile: row.mobile_number,
            reason: "Invalid format",
          });
        }
      });

      if (invalid.length) {
        let html =
          '<div class="text-danger">Found ' +
          invalid.length +
          ' invalid numbers:</div><table class="table table-bordered">';
        html +=
          "<thead><tr><th>Row</th><th>Number</th><th>Reason</th></tr></thead><tbody>";

        invalid.forEach(function (row) {
          html +=
            "<tr><td>" +
            row.idx +
            "</td><td>" +
            row.mobile +
            "</td><td>" +
            row.reason +
            "</td></tr>";
        });

        html += "</tbody></table>";

        frappe.msgprint({
          title: __("Validation Results"),
          indicator: "red",
          message: html,
        });

    } else {
        frappe.msgprint({
          title: __("Validation Results"),
          indicator: "green",
          message: __("All recipients have valid numbers"),
        });
      }
    });
  },
});