import frappe


def get_ip_address():
    if frappe.request:
        headers = frappe.request.headers
        if "X-Forwarded-For" in headers:
            return headers["X-Forwarded-For"]
        elif "X-Real-IP" in headers:
            return headers["X-Real-IP"]
        elif "HTTP_CLIENT_IP" in frappe.request.environ:
            return frappe.request.environ["HTTP_CLIENT_IP"]
        elif "HTTP_X_FORWARDED_FOR" in frappe.request.environ:
            return frappe.request.environ["HTTP_X_FORWARDED_FOR"]
        elif "HTTP_X_FORWARDED" in frappe.request.environ:
            return frappe.request.environ["HTTP_X_FORWARDED"]
        else:
            return frappe.request.remote_addr
    return None
