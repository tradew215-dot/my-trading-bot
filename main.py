from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import threading
import time


# Render Health Check पास करने के लिए वेब सर्वर
class SimpleHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"Bot is active and running!")


def run_server():
  port = int(os.environ.get("PORT", 8080))
  server = HTTPServer(("0.0.0.0", port), SimpleHandler)
  server.serve_forever()


# बैकग्राउंड में वेब सर्वर चालू करें
threading.Thread(target=run_server, daemon=True).start()

print("Bot active and running...")

# बॉट का लूप
while True:
  time.sleep(60)
