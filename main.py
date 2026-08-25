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
# RENDER & UPTIMEROBOT WEB SERVER (HEALTH CHECK)
# -------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running alive!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        return

def start_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"🌐 वेब सर्वर पोर्ट {port} पर चालू हो गया है...")
    server.serve_forever()

# -------------------------------------------------------------
# RENDER CONFIGURATION (ENVIRONMENT VARIABLES)
# -------------------------------------------------------------
COSMIC_API_KEY = os.getenv("COSMIC_API_KEY", "")
COSMIC_API_SECRET = os.getenv("COSMIC_API_SECRET", "")
LIVE_TRADING_ENV = os.getenv("LIVE_TRADING", "0")
LIVE_TRADING = True if LIVE_TRADING_ENV == "1" else False

# Trading Settings
SYMBOL = "BTCUSDT"       # ब्रोकर के अनुसार सही सिंबल BTCUSDT है
TRADE_AMOUNT = 0.02      # प्रति ट्रेड BTC की मात्रा (0.02 फिक्स की गई)

# -------------------------------------------------------------
# BROKER CLASS
# -------------------------------------------------------------
class CosmicBroker:
    def __init__(self, api_key, api_secret, live_trading=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.live_trading = live_trading
        self.base_url = "https://api.cosmictrade.io"  # ब्रोकर API बेस URL

    def get_market_price(self, symbol):
        """वर्तमान मार्केट प्राइस प्राप्त करना"""
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                return float(res.json().get("price", 0))
        except Exception as e:
            logging.error(f"मूल्य प्राप्त करने में त्रुटि ({symbol}): {e}")
        return None

    def get_1m_signal(self, symbol):
        """1-मिनट (1m) टाइमफ्रेम पर सिग्नल प्राप्त करना"""
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=2"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                open_p = float(data[-1][1])
                close_p = float(data[-1][4])
                return "BUY" if close_p >= open_p else "SELL"
        except Exception as e:
            logging.error(f"1m सिग्नल त्रुटि ({symbol}): {e}")
        return None

    def execute_order(self, symbol, side, amount):
        """ट्रेड ऑर्डर निष्पादित करना"""
        if not self.live_trading:
            logging.info(f"🧪 [PAPER MODE] सिमुलेशन ट्रेड -> {side} {amount} {symbol}")
            return {"status": "SUCCESS", "mode": "PAPER"}

        logging.info(f"🚨 [LIVE MODE] ब्रोकर को ऑर्डर भेजा जा रहा है -> {side} {amount} {symbol}")
        
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "symbol": symbol,
            "side": side.upper(),   # BUY या SELL
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
    # वेब सर्वर को बैकग्राउंड थ्रेड में चालू करना
    server_thread = threading.Thread(target=start_health_check_server, daemon=True)
    server_thread.start()

    logging.info("==========================================")
    logging.info(f"🤖 ट्रेडिंग बॉट प्रारंभ हो रहा है...")
    logging.info(f"सिंबल (Pair): {SYMBOL}")
    logging.info(f"लॉट साइज: {TRADE_AMOUNT}")
    logging.info(f"लाइव ट्रेडिंग स्थिति: {'🔴 LIVE (असली पैसा)' if LIVE_TRADING else '🟢 PAPER (सिमुलेशन)'}")
    logging.info("==========================================")

    broker = CosmicBroker(COSMIC_API_KEY, COSMIC_API_SECRET, live_trading=LIVE_TRADING)
    
    current_position = None  # None, 'BUY', 'SELL'

    while True:
        try:
            price = broker.get_market_price(SYMBOL)
            signal = broker.get_1m_signal(SYMBOL)

            if price and signal:
                logging.info(f"📊 {SYMBOL} मूल्य: ${price:.2f} | 1m सिग्नल: {signal}")

                # 1m सिग्नल के अनुसार ट्रेड निष्पादित करना
                if signal == "BUY" and current_position != "BUY":
                    broker.execute_order(SYMBOL, "BUY", TRADE_AMOUNT)
                    current_position = "BUY"
                elif signal == "SELL" and current_position != "SELL":
                    broker.execute_order(SYMBOL, "SELL", TRADE_AMOUNT)
                    current_position = "SELL"

            time.sleep(10)  # हर 10 सेकंड में 1m कैंडल डेटा चेक करेगा

        except Exception as e:
            logging.error(f"मेन लूप एरर: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
