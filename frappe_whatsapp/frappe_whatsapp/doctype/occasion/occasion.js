// Copyright (c) 2025, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("Occasion", {
  refresh(frm) {
    frm.set_query("invite_template", function () {
      return {
        filters: {
          status: "APPROVED",
        },
      };
    });

    frm.set_query("confirmed_template", function () {
      return {
        filters: {
          status: "APPROVED",
        },
      };
    });

    frm.set_query("declined_template", function () {
      return {
        filters: {
          status: "APPROVED",
        },
      };
    });
  },
});
