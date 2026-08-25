import os
import time
import logging
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

COSMIC_API_KEY = os.getenv("COSMIC_API_KEY", "")
COSMIC_API_SECRET = os.getenv("COSMIC_API_SECRET", "")
LIVE_TRADING = os.getenv("LIVE_TRADING", "0") == "1"

SYMBOL = "BTCUSDT"
TIMEFRAME = "1m"
TRADE_AMOUNT = 0.02
CHECK_SECONDS = 5


class CosmicBroker:
    def __init__(self, api_key, api_secret, live_trading=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.live_trading = live_trading
        self.base_url = "https://api.cosmictrade.io"

    def get_market_price(self, symbol):
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=5
            )
            if r.status_code == 200:
                return float(r.json()["price"])
            logging.error("Price API HTTP %s", r.status_code)
        except Exception as e:
            logging.error("Price error: %s", e)
        return None

    def get_1m_candles(self, symbol, limit=60):
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": "1m",
                    "limit": limit
                },
                timeout=5
            )
            if r.status_code != 200:
                logging.error("1m candle HTTP %s", r.status_code)
                return []

            data = r.json()
            if len(data) < 10:
                return []

            # Current unfinished candle is excluded.
            return [
                {
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "time": int(k[0])
                }
                for k in data[:-1]
            ]
        except Exception as e:
            logging.error("1m candle error: %s", e)
            return []

    def get_1m_signal(self, candles):
        if len(candles) < 10:
            return "WAIT"

        last = candles[-1]
        prev = candles[-2]

        candle_range = last["high"] - last["low"]
        if candle_range <= 0:
            return "WAIT"

        body = abs(last["close"] - last["open"])

        # Avoid very weak/doji candles.
        if body < candle_range * 0.20:
            return "WAIT"

        if last["close"] > last["open"] and last["close"] > prev["close"]:
            return "BUY"

        if last["close"] < last["open"] and last["close"] < prev["close"]:
            return "SELL"

        return "WAIT"

    def execute_order(self, symbol, side, amount):
        if not self.live_trading:
            logging.info(
                "[PAPER] %s %.6f %s",
                side, amount, symbol
            )
            return {
                "status": "SUCCESS",
                "mode": "PAPER",
                "side": side,
                "quantity": amount
            }

        if not self.api_key or not self.api_secret:
            logging.error("COSMIC_API_KEY / COSMIC_API_SECRET missing")
            return None

        # IMPORTANT:
        # This is the endpoint/header format from your supplied code.
        # Do not assume it is correct for Cosmic until Cosmic confirms it.
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
            response = requests.post(
                f"{self.base_url}/v1/order",
                json=payload,
                headers=headers,
                timeout=10
            )

            try:
                result = response.json()
            except Exception:
                result = {
                    "status_code": response.status_code,
                    "text": response.text
                }

            logging.info("Broker response: %s", result)
            return result

        except Exception as e:
            logging.error("Live order error: %s", e)
            return None


def run_bot():
    logging.info("==========================================")
    logging.info("1-MINUTE LOW-CAPITAL BOT STARTED")
    logging.info("Symbol: %s", SYMBOL)
    logging.info("Timeframe: %s", TIMEFRAME)
    logging.info("Lot size: %.6f BTC", TRADE_AMOUNT)
    logging.info(
        "Mode: %s",
        "LIVE" if LIVE_TRADING else "PAPER"
    )
    logging.info("==========================================")

    broker = CosmicBroker(
        COSMIC_API_KEY,
        COSMIC_API_SECRET,
        LIVE_TRADING
    )

    current_position = None
    last_signal_candle = None

    while True:
        try:
            price = broker.get_market_price(SYMBOL)
            if price:
                logging.info(
                    "%s 1m price: $%.2f",
                    SYMBOL, price
                )

            candles = broker.get_1m_candles(SYMBOL, 60)
            if not candles:
                time.sleep(CHECK_SECONDS)
                continue

            signal = broker.get_1m_signal(candles)
            candle_time = candles[-1]["time"]

            logging.info("1m signal: %s", signal)

            # One order attempt per closed 1m candle.
            if (
                candle_time != last_signal_candle
                and signal in ("BUY", "SELL")
            ):
                if signal != current_position:
                    result = broker.execute_order(
                        SYMBOL,
                        signal,
                        TRADE_AMOUNT
                    )

                    if result and (
                        result.get("status") in ("SUCCESS", "FILLED")
                        or result.get("mode") == "PAPER"
                        or result.get("ok") is True
                    ):
                        current_position = signal
                        logging.info(
                            "ORDER SUCCESS: %s | Qty %.6f",
                            signal, TRADE_AMOUNT
                        )
                    else:
                        logging.error(
                            "ORDER NOT CONFIRMED: %s",
                            result
                        )

                last_signal_candle = candle_time

            time.sleep(CHECK_SECONDS)

        except KeyboardInterrupt:
            logging.info("Bot stopped.")
            break
        except Exception as e:
            logging.error("Main loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
