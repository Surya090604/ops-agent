"""Mock company environment: a vendor portal (read) + an internal billing system (write).

Deliberately imperfect so the agent's recovery paths are exercised:
  * Portal dates are human-formatted ("Nov 14, 2026"); the internal system requires YYYY-MM-DD (-> 422).
  * The first write to the internal system fails with a 503 (-> retry).
Stdlib only.
"""
import json
import re
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VENDORS = {
    "acme": {"name": "Acme Corp", "invoices": [
        {"no": "INV-1041", "issued": "Aug 02, 2026", "amount": "$1,200.00", "due": "Sep 01, 2026"},
        {"no": "INV-1077", "issued": "Sep 15, 2026", "amount": "$3,450.75", "due": "Nov 14, 2026"},
        {"no": "INV-1063", "issued": "Sep 01, 2026", "amount": "$980.00", "due": "Oct 01, 2026"},
    ]},
    "globex": {"name": "Globex Ltd", "invoices": [
        {"no": "GX-220", "issued": "Oct 01, 2026", "amount": "$15,000.00", "due": "Dec 01, 2026"},
    ]},
}

STATE = {"bills": [], "post_attempts": 0, "fail_first_post": True}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="text/html"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, "<h1>Mock Company Network</h1><ul>"
                              "<li><a href='/vendor/'>Vendor portal</a></li>"
                              "<li><a href='/internal/bills'>Internal billing system</a> "
                              "(POST vendor, invoice_no, amount, due_date)</li></ul>")
        if u.path == "/vendor/":
            links = "".join(f"<li><a href='/vendor/invoices?company={k}'>{v['name']}</a></li>" for k, v in VENDORS.items())
            return self._send(200, f"<h1>Vendor Portal</h1><p>Companies:</p><ul>{links}</ul>")
        if u.path == "/vendor/invoices":
            key = (q.get("company", [""])[0]).lower()
            v = VENDORS.get(key)
            if not v:
                return self._send(404, f"<h1>Not found</h1><p>No company '{escape(key)}'. See /vendor/</p>")
            rows = "".join(
                f"<tr><td>{i['no']}</td><td>{i['issued']}</td><td>{i['amount']}</td><td>{i['due']}</td></tr>"
                for i in v["invoices"])
            return self._send(200, f"<h1>{v['name']} invoices</h1><table>"
                              f"<tr><th>Invoice</th><th>Issued</th><th>Amount</th><th>Due</th></tr>{rows}</table>")
        if u.path == "/internal/bills":
            with LOCK:
                return self._send(200, json.dumps(STATE["bills"], indent=2), "application/json")
        if u.path == "/__reset":
            with LOCK:
                STATE.update(bills=[], post_attempts=0, fail_first_post=True)
            return self._send(200, "reset")
        self._send(404, "not found")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/internal/bills":
            return self._send(404, "not found")
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        with LOCK:
            STATE["post_attempts"] += 1
            if STATE["fail_first_post"] and STATE["post_attempts"] == 1:
                return self._send(503, json.dumps({"error": "service temporarily unavailable"}), "application/json")
        missing = [f for f in ("vendor", "invoice_no", "amount", "due_date") if not form.get(f)]
        if missing:
            return self._send(422, json.dumps({"error": f"missing fields: {missing}"}), "application/json")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", form["due_date"]):
            return self._send(422, json.dumps({"error": "due_date must be YYYY-MM-DD"}), "application/json")
        if not re.fullmatch(r"\d+(\.\d{1,2})?", form["amount"]):
            return self._send(422, json.dumps({"error": "amount must be a plain number like 1234.50 (no $ or commas)"}), "application/json")
        with LOCK:
            if any(b["invoice_no"] == form["invoice_no"] for b in STATE["bills"]):
                return self._send(409, json.dumps({"error": "invoice already recorded"}), "application/json")
            rec = {k: form[k] for k in ("vendor", "invoice_no", "amount", "due_date")}
            STATE["bills"].append(rec)
        self._send(201, json.dumps({"ok": True, "record": rec}), "application/json")


def serve(port=8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


if __name__ == "__main__":
    print("Mock company app on http://127.0.0.1:8765  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
