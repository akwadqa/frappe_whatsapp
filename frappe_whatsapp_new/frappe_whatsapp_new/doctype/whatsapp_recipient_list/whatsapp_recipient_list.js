frappe.ui.form.on("WhatsApp Recipient List", {
  occasion: function (frm) {
    frm.set_value("import_filters", `{"occasion": "${frm.doc.occasion}"}`);
  },
  refresh: function (frm) {
    frm.fields_dict["recipients"].grid.get_field("occasion_invitee").get_query = function (doc, cdt, cdn) {
      if (!frm.doc.occasion) {
        frappe.msgprint(__("Please select an Occasion first."));
        return false;
      }

      return {
        filters: {
          occasion: frm.doc.occasion,
        },
      };
    };

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
        method: "frappe_whatsapp_new.utils.bulk_messaging.import_recipients",
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