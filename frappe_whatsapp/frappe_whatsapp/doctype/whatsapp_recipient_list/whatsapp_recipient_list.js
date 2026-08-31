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

    // Add a button to validate all recipients
    frm.add_custom_button(__("Validate Recipients"), function () {
      let invalid = [];
      let seen = {};
      let duplicate_groups = {};

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
            name: row.name,
            mobile: row.mobile_number,
            reason: "Invalid format",
          });
        }

        if (mobile) {
          if (!seen[mobile]) {
            seen[mobile] = [];
          }
          seen[mobile].push({
            idx: idx + 1,
            name: row.name,
            recipient_name: row.recipient_name || "",
          });
        }
      });

      Object.keys(seen).forEach(function (mobile) {
        if (seen[mobile].length > 1) {
          duplicate_groups[mobile] = seen[mobile];
        }
      });

      let duplicate_count = Object.keys(duplicate_groups).length;

      if (!invalid.length && !duplicate_count) {
        frappe.msgprint({
          title: __("Validation Results"),
          indicator: "green",
          message: __("All recipients have valid, unique numbers"),
        });
        return;
      }

      let html = "";

      if (invalid.length) {
        html +=
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
      }

      if (duplicate_count) {
        html +=
          '<div class="text-danger" style="margin-top: 10px;">Found ' +
          duplicate_count +
          ' duplicate number(s):</div><table class="table table-bordered">';
        html +=
          "<thead><tr><th>Number</th><th>Rows</th><th>Name(s)</th></tr></thead><tbody>";

        Object.keys(duplicate_groups).forEach(function (mobile) {
          let rows = duplicate_groups[mobile].map((r) => r.idx).join(", ");
          let names = duplicate_groups[mobile]
            .map((r) => r.recipient_name)
            .filter((n) => n)
            .join(", ");
          html +=
            "<tr><td>" +
            mobile +
            "</td><td>" +
            rows +
            "</td><td>" +
            names +
            "</td></tr>";
        });

        html += "</tbody></table>";
      }

      let dialog = new frappe.ui.Dialog({
        title: __("Validation Results"),
        fields: [
          {
            fieldtype: "HTML",
            fieldname: "validation_results",
            options: html,
          },
        ],
        primary_action_label:
          duplicate_count || invalid.length
            ? __("Remove All (Duplicates and Invalid)")
            : __("Close"),
        primary_action: function () {
          if (duplicate_count || invalid.length) {
            let rows_to_remove = new Set();

            Object.keys(duplicate_groups).forEach(function (mobile) {
              // Keep the first occurrence, remove the rest
              duplicate_groups[mobile].slice(1).forEach(function (r) {
                rows_to_remove.add(r.name);
              });
            });

            invalid.forEach(function (row) {
              rows_to_remove.add(row.name);
            });

            frm.doc.recipients = frm.doc.recipients.filter(
              (row) => !rows_to_remove.has(row.name)
            );
            frm.refresh_field("recipients");
            frm.dirty();

            frappe.msgprint({
              title: __("Recipients Removed"),
              indicator: "green",
              message: __("Removed {0} recipient(s) (duplicates and invalid numbers)", [
                rows_to_remove.size,
              ]),
            });
          }

          dialog.hide();
        },
        secondary_action_label:
          duplicate_count || invalid.length ? __("Close") : null,
        secondary_action: function () {
          dialog.hide();
        },
      });

      dialog.show();
    });
  },
});