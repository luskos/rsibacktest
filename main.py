import ccxt
import pandas as pd
import numpy as np
import websocket
import json
import os
import time
import logging

# -----------------------------
# 1. Set up Binance Testnet API
# -----------------------------
api_key = 'krw2RrP26ttZgRrC2Lw8N9yF6TeqsUXM3kzplDON9a8crjAkohpFwuL8SIEOFd7e'
api_secret = 'UAqSx1RxvGjQUVOUd4KEarIvokcXio0MLQogQlD7LLinmzREj4zYWBbRu9anLiMg'
binance = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'urls': {
        'api': {
            'public': 'https://testnet.binance.vision/api/v3',
            'private': 'https://testnet.binance.vision/api/v3',
        },
    },
})

# -----------------------------
# 2. Define Parameters
# -----------------------------
symbol = 'BTC/USDC'  # Use USDT instead of USDC on Testnet
timeframe = '1h'

# Risk management parameters
risk_reward_ratio = 0.25
atr_multiplier = 1.5
risk_per_trade = 0.25

# Trailing stop parameters
trailing_stop_enabled = True
trailing_stop_multiplier = 1.0

# Grid trading parameters
max_grid_trades = 1
grid_spacing_factor = 2.0
grid_exit_factor = 1.0
min_avg_grid_profit = -0.02

# Initialize balance and trade tracking
balance = float(binance.fetch_balance()['total']['USDC'])  # Fetch initial balance
open_trades = []
closed_trades = []

# Logging
logging.basicConfig(filename='trading_bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Starting live trading bot")

# -----------------------------
# 3. Indicator Calculations
# -----------------------------
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_indicators(df):
    df['rsi'] = calculate_rsi(df['close'], period=14)
    rolling_mean = df['close'].rolling(20).mean()
    rolling_std = df['close'].rolling(15).std()
    df['boll_upper'] = rolling_mean + 2 * rolling_std
    df['boll_lower'] = rolling_mean - 1 * rolling_std
    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    df['sma50'] = df['close'].rolling(45).mean()
    df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema12'] - df['ema26']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

# -----------------------------
# 4. Trade Management Functions
# -----------------------------
def open_trade(trade_type, price, atr_value):
    stop_loss_distance = atr_value * atr_multiplier
    position_size = (balance * risk_per_trade) / stop_loss_distance
    if trade_type == 1:  # Long
        stop_loss = price - stop_loss_distance
        take_profit = price + stop_loss_distance * risk_reward_ratio
    else:  # Short
        stop_loss = price + stop_loss_distance
        take_profit = price - stop_loss_distance * risk_reward_ratio
    return {
        'type': trade_type,
        'entry_price': price,
        'position_size': position_size,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
    }

def check_trade_exit(trade, candle):
    if trade['type'] == 1:
        if candle['low'] <= trade['stop_loss'] or candle['high'] >= trade['take_profit']:
            return True
    else:
        if candle['high'] >= trade['stop_loss'] or candle['low'] <= trade['take_profit']:
            return True
    return False

def update_trade_trailing_stop(trade, current_close, atr_value):
    if trade['type'] == 1:
        new_stop = current_close - (atr_value * trailing_stop_multiplier)
        if new_stop > trade['stop_loss']:
            trade['stop_loss'] = new_stop
    else:
        new_stop = current_close + (atr_value * trailing_stop_multiplier)
        if new_stop < trade['stop_loss']:
            trade['stop_loss'] = new_stop

# -----------------------------
# 5. WebSocket for Real-Time Data
# -----------------------------
def on_message(ws, message):
    data = json.loads(message)
    candle = data['k']
    latest_candle = {
        'timestamp': candle['t'],
        'open': float(candle['o']),
        'high': float(candle['h']),
        'low': float(candle['l']),
        'close': float(candle['c']),
        'volume': float(candle['v']),
    }
    process_new_candle(latest_candle)

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info("WebSocket connection closed")

def on_open(ws):
    logging.info("WebSocket connection opened")

# Start WebSocket connection
websocket_url = "wss://testnet.binance.vision/ws/btcusdc@kline_1h"
ws = websocket.WebSocketApp(websocket_url, on_message=on_message, on_error=on_error, on_close=on_close)
ws.on_open = on_open

# -----------------------------
# 6. Live Trading Logic
# -----------------------------
def process_new_candle(candle):
    global balance, open_trades, closed_trades

    # Create a DataFrame for the latest candle
    df = pd.DataFrame([candle])
    calculate_indicators(df)

    current_price = df['close'].iloc[-1]
    atr_value = df['atr'].iloc[-1]

    # Check for exit conditions on open trades
    trades_to_close = []
    for idx, trade in enumerate(open_trades):
        if check_trade_exit(trade, candle):
            side = 'sell' if trade['type'] == 1 else 'buy'
            quantity = trade['position_size']
            order = binance.create_order(symbol, 'market', side, quantity)
            if order:
                profit = (current_price - trade['entry_price']) * trade['position_size'] if trade['type'] == 1 else (trade['entry_price'] - current_price) * trade['position_size']
                balance += profit
                closed_trades.append({**trade, 'exit_price': current_price, 'profit': profit})
                trades_to_close.append(idx)
                logging.info(f"Closed trade: {trade}, Profit: {profit:.2f} USDC")

    # Remove closed trades
    for idx in sorted(trades_to_close, reverse=True):
        del open_trades[idx]

    # Check for entry signals
    entry_signal = calculate_entry_signal(df)
    if entry_signal != 0 and len(open_trades) < max_grid_trades:
        new_trade = open_trade(entry_signal, current_price, atr_value)
        side = 'buy' if entry_signal == 1 else 'sell'
        quantity = new_trade['position_size']
        order = binance.create_order(symbol, 'market', side, quantity)
        if order:
            open_trades.append(new_trade)
            logging.info(f"Opened new trade: {new_trade}")

# Start WebSocket in a separate thread
import threading
ws_thread = threading.Thread(target=ws.run_forever)
ws_thread.start()

# Keep the main thread alive
while True:
    time.sleep(1)
