import re
from frappe.model.document import Document


def get_value_from_childtable(doc, fieldname):
    """Retrieve a field value from a child table row.

    Supports paths like: items[0].against_sales_order
    """
    path_parts = re.split(r'\.(?![^\[]*\])', fieldname)
    current_value = doc

    for segment in path_parts:
        list_match = re.match(r'(\w+)\[(\d+)\]$', segment)
        if list_match:
            table_fieldname, row_index = list_match.groups()
            row_index = int(row_index)

            child_rows = getattr(current_value, table_fieldname, None)
            if child_rows is None and isinstance(current_value, dict):
                child_rows = current_value.get(table_fieldname)

            if isinstance(child_rows, list) and len(child_rows) > row_index:
                current_value = child_rows[row_index]
            else:
                return None
        else:
            if isinstance(current_value, (Document, dict)):
                current_value = (
                    current_value.get(segment)
                    if isinstance(current_value, dict)
                    else current_value.get(segment)
                )
            else:
                return None

    return current_value