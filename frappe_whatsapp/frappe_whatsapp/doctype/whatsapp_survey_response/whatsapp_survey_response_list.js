frappe.listview_settings["WhatsApp Survey Response"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Survey Response Report"), () => {
			frappe.set_route("query-report", "Survey Response Report");
		});
	},
};
