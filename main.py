import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import os
import logging
 
# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
 
# Set up Binance API with your API key and secret
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')
binance = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})
 
# Trading parameters
symbol = 'ETH/USDC'
timeframe = '1m'  # Use 1-minute candles
trading_fee = 0.00075  # 0.075% Binance trading fee
rsi_period = 14  # RSI period
rsi_buy_threshold = 31  # Buy when RSI < 31
rsi_sell_threshold = 60  # Sell when RSI > 70
take_profit = 0.04  # 4% take profit
stop_loss = 0.02  # 2% stop loss
 
# Initialize variables
position = 0  # 0 = no position, 1 = long position
entry_price = 0  # Price at which the position was opened
trade_history = []
 
def fetch_initial_data():
    """Fetch historical OHLCV data from Binance"""
    logging.info("Fetching initial OHLCV data...")
    ohlcv = binance.fetch_ohlcv(symbol, timeframe, limit=100)  # Fetch 100 candles
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df
 
def fetch_latest_candle():
    """Fetch the latest candle for live trading"""
    ohlcv = binance.fetch_ohlcv(symbol, timeframe, limit=1)  # Fetch the latest 1-minute candle
    latest_candle = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    latest_candle['timestamp'] = pd.to_datetime(latest_candle['timestamp'], unit='ms')
    return latest_candle.iloc[0]
 
def calculate_rsi(data, period=14):
    """Calculate RSI (Relative Strength Index)"""
    delta = data['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
 
 
def get_usdc_balance():
    """Fetch the USDC balance from the Binance account"""
    try:
        balance = binance.fetch_balance()
        usdc_balance = balance['total']['USDC']
        logging.info(f"Current USDC Balance: {usdc_balance}")
        return usdc_balance
    except Exception as e:
        logging.error(f"Error fetching USDC balance: {e}")
        return None
 
def place_order(side, amount, price):
    """Place a market order on Binance"""
    try:
        order = binance.create_order(symbol, 'market', side, amount, price)
        logging.info(f"Order placed: {order}")
        return order
    except Exception as e:
        logging.error(f"Error placing order: {e}")
        return None
 
def get_eth_balance():
    """Fetch the ETH balance from the Binance account"""
    try:
        balance = binance.fetch_balance()
        eth_balance = balance['total'].get('ETH', 0)  # Ensure ETH key exists
        logging.info(f"Current ETH Balance: {eth_balance}")
        return eth_balance
    except Exception as e:
        logging.error(f"Error fetching ETH balance: {e}")
        return None
 
def live_trade():
    """Live trading function"""
    logging.info("Starting live trading...")
 
    # Fetch initial data
    ohlcv_data = fetch_initial_data()
    global position, entry_price  # Use global variables to retain state
 
    while True:
        try:
            # Fetch the latest candle
            latest_candle = fetch_latest_candle()
            ohlcv_data = pd.concat([ohlcv_data.iloc[1:], pd.DataFrame([latest_candle])], ignore_index=True)
 
            # Calculate RSI and SMA50
            ohlcv_data['RSI'] = calculate_rsi(ohlcv_data, period=rsi_period)
 
            latest_row = ohlcv_data.iloc[-1]
            current_price = latest_row['close']
            rsi = latest_row['RSI']
 
            logging.info(f"Current Price: {current_price:.2f} USDC, RSI: {rsi:.2f}")
 
            # Fetch USDC balance
            usdc_balance = get_usdc_balance()
            if usdc_balance is None:
                time.sleep(10)
                continue
 
            # Calculate trade amount (leave $1 margin)
            trade_amount = max(usdc_balance - 2, 0)
 
            # Buy condition: RSI < threshold AND price > SMA50
            if rsi < rsi_buy_threshold and position == 0:
                amount = (trade_amount / current_price) * (1 - trading_fee)  # Apply fee
                order = place_order('buy', amount, current_price)
                if order:
                    position = 1
                    entry_price = current_price
                    logging.info(f"BUY {amount:.6f} ETH at {entry_price:.2f} USDC")
 
            # Sell condition
            if rsi > rsi_sell_threshold and position == 1:
                eth_balance = get_eth_balance()
                if eth_balance and eth_balance > 0:
                    order = place_order('sell', eth_balance, current_price)
                    if order:
                        position = 0
                        exit_price = current_price
                        pnl = (exit_price - entry_price) * eth_balance
                        trade_history.append({'entry_price': entry_price, 'exit_price': exit_price, 'PnL': pnl})
                        logging.info(f"SELL {eth_balance:.6f} ETH at {exit_price:.2f} USDC | PnL: {pnl:.2f} USDC")
                else:
                    logging.warning("Insufficient ETH balance for selling.")
 
           # Stop-loss and take-profit conditions
 
            if position == 1:
 
                if current_price >= entry_price * (1 + take_profit):
 
                    amount = binance.fetch_balance()['total']['ETH']
 
                    order = place_order('sell', amount, current_price)
 
                    if order:
 
                        position = 0
 
                        exit_price = current_price
 
                        pnl = (exit_price - entry_price) * amount
 
                        trade_history.append({'entry_price': entry_price, 'exit_price': exit_price, 'PnL': pnl})
 
                        logging.info(f"TAKE PROFIT: SELL {amount:.6f} ETH at {exit_price:.2f} USDC | PnL: {pnl:.2f} USDC")
 
                elif current_price <= entry_price * (1 - 0.01):  # 1% stop-loss
 
                    amount = binance.fetch_balance()['total']['ETH']
 
                    order = place_order('sell', amount, current_price)
 
                    if order:
 
                        position = 0
 
                        exit_price = current_price
 
                        pnl = (exit_price - entry_price) * amount
 
                        trade_history.append({'entry_price': entry_price, 'exit_price': exit_price, 'PnL': pnl})
 
                        logging.info(f"STOP LOSS: SELL {amount:.6f} ETH at {exit_price:.2f} USDC | PnL: {pnl:.2f} USDC")
 
                        # Re-enter buy position after stop-loss
 
                        if rsi < rsi_buy_threshold:
 
                            amount = (trade_amount / current_price) * (1 - trading_fee)  # Apply fee
 
                            order = place_order('buy', amount, current_price)
 
                            if order:
 
                                position = 1
 
                                entry_price = current_price
 
                                logging.info(f"RE-ENTER BUY {amount:.6f} ETH at {entry_price:.2f} USDC")
            time.sleep(60)  # Wait for the next candle
        except Exception as e:
            logging.error(f"Error in live trading loop: {e}")
            time.sleep(10)
 
# Start live trading
live_trade()
