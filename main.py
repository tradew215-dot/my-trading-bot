import os
import time
import logging
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# -------------------------------------------------------------
# LOGGING SETUP
# -------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -------------------------------------------------------------
# WEB SERVER FOR RENDER & UPTIMEROBOT (HEALTH CHECK)
# -------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        return

def start_health_check_server():
    try:
        port_env = os.getenv("PORT", "8080")
        port = int(port_env)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"🌐 वेब सर्वर पोर्ट {port} पर सफलतापूर्वक चालू हो गया है...")
        server.serve_forever()
    except Exception as e:
        logging.error(f"वेब सर्वर त्रुटि: {e}")

# -------------------------------------------------------------
# CONFIGURATION (ENVIRONMENT VARIABLES)
# -------------------------------------------------------------
COSMIC_API_KEY = os.getenv("COSMIC_API_KEY", "")
COSMIC_API_SECRET = os.getenv("COSMIC_API_SECRET", "")
LIVE_TRADING_ENV = os.getenv("LIVE_TRADING", "0")
LIVE_TRADING = True if LIVE_TRADING_ENV == "1" else False

# Trading Settings
SYMBOL = "BTCUSDT"
TRADE_AMOUNT = 0.02

# -------------------------------------------------------------
# BROKER CLASS
# -------------------------------------------------------------
class CosmicBroker:
    def __init__(self, api_key, api_secret, live_trading=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.live_trading = live_trading
        self.base_url = "https://api.cosmic.trade"

    def get_market_price(self, symbol):
        """MEXC API से लाइव प्राइस (Render IP restriction bypass करने के लिए)"""
        try:
            url = f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                return float(res.json().get("price", 0))
            else:
                logging.error(f"Price API Error: Status {res.status_code}")
        except Exception as e:
            logging.error(f"मूल्य प्राप्त करने में त्रुटि ({symbol}): {e}")
        return None

    def get_1m_signal(self, symbol):
        """MEXC API से 1m कैंडल सिग्नल"""
        try:
            url = f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval=1m&limit=2"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                open_p = float(data[-1][1])
                close_p = float(data[-1][4])
                return "BUY" if close_p >= open_p else "SELL"
            else:
                logging.error(f"1m Candle API Error: Status {res.status_code}")
        except Exception as e:
            logging.error(f"1m सिग्नल त्रुटि ({symbol}): {e}")
        return None

    def execute_order(self, symbol, side, amount):
        """लाइव ब्रोकर को ऑर्डर भेजना"""
        if not self.live_trading:
            logging.info(f"🧪 [PAPER MODE] सिमुलेशन ट्रेड -> {side} {amount} {symbol}")
            return {"status": "SUCCESS", "mode": "PAPER"}

        logging.info(f"🚨 [LIVE MODE] Cosmic Trade को ऑर्डर भेजा जा रहा है -> {side} {amount} {symbol}")
        
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": amount
        }
        
        try:
            endpoint = f"{self.base_url}/v1/order"
            response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
            result = response.json()
            logging.info(f"ब्रोकर प्रतिक्रिया: {result}")
            return result
        except Exception as e:
            logging.error(f"लाइव ऑर्डर भेजने में त्रुटि: {e}")
            return None

# -------------------------------------------------------------
# MAIN BOT ENGINE
# -------------------------------------------------------------
def run_bot():
    server_thread = threading.Thread(target=start_health_check_server, daemon=True)
    server_thread.start()

    logging.info("==========================================")
    logging.info("🤖 ट्रेडिंग बॉट प्रारंभ हो रहा है...")
    logging.info(f"सिंबल: {SYMBOL} | लॉट साइज: {TRADE_AMOUNT}")
    logging.info(f"लाइव ट्रेडिंग: {'🔴 LIVE' if LIVE_TRADING else '🟢 PAPER'}")
    logging.info("==========================================")

    broker = CosmicBroker(COSMIC_API_KEY, COSMIC_API_SECRET, live_trading=LIVE_TRADING)
    current_position = None

    while True:
        try:
            price = broker.get_market_price(SYMBOL)
            signal = broker.get_1m_signal(SYMBOL)

            if price and signal:
                logging.info(f"📊 {SYMBOL} मूल्य: ${price:.2f} | 1m सिग्नल: {signal}")

                if signal == "BUY" and current_position != "BUY":
                    broker.execute_order(SYMBOL, "BUY", TRADE_AMOUNT)
                    current_position = "BUY"
                elif signal == "SELL" and current_position != "SELL":
                    broker.execute_order(SYMBOL, "SELL", TRADE_AMOUNT)
                    current_position = "SELL"

            time.sleep(10)

        except Exception as e:
            logging.error(f"मेन लूप त्रुटि: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
