import asyncio
import datetime
from dataclasses import dataclass
import ccxt.pro as ccxt
import pandas as pd
import pandas_ta as ta
from logging import exception

def current_utc_ms():

    return datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
        ).timestamp() * 1000

def calculate_percent(percent, price):

    return percent * (price / 100)

@dataclass
class settings:

    # API Keys
    api_key = ''
    api_secret = ''

    # Symbol
    symbol = "BTC/USDT:USDT"

    # OHLCV settings
    timeframe = "1m"
    ohlcv_data = []
    days_to_load = 0    # If days_to_load is set to zero, the data 
                        # will be loaded from the start of the current day.

    # Position settings
    order_size = 0.001
    take_profit_percent = 1.0
    stop_loss_percent = 1.0

    # Scale orders settings
    enable_scale_orders = True
    scale_orders_size = 0.001
    scale_in_times = 4
    scale_in_percent = 0.25
    cancel_all_orders_on_close = True   # Cancel all scale orders when the 
                                        # position is closed.
    in_position = False

    # Cooldown settings
    use_cooldown = True
    cooldown_timer = 60
    cooldown = False

    # Lock
    lock = True
    lock_offset = 0.2

# Setup exchange and API connection
exchange = ccxt.bybit({
    'apiKey': settings.api_key,
    'secret': settings.api_secret,
    'enableRateLimit': True})

# Cooldown after position is entered.
async def cooldown_pos():

    await asyncio.sleep(settings.cooldown_timer)

    settings.cooldown = False

async def create_scale_orders(last_price):

    static_last_price = last_price
    tasks = []

    for i in range(settings.scale_in_times):

        static_last_price -= calculate_percent(
            settings.scale_in_percent, static_last_price)

        order_price = exchange.price_to_precision(
            settings.symbol, static_last_price)

        order_size = exchange.amount_to_precision(
            settings.symbol, settings.scale_orders_size)

        tasks.append(exchange.create_limit_buy_order(
            symbol=settings.symbol,
            amount=order_size,
            price=order_price))

    await asyncio.gather(*tasks)
    settings.in_position = True

async def create_market_order(last_price):

    tp = exchange.price_to_precision(
        settings.symbol, 
        last_price + calculate_percent(settings.take_profit_percent, last_price))

    st = exchange.price_to_precision(
        settings.symbol, 
        last_price - calculate_percent(settings.stop_loss_percent, last_price))

    order_size = exchange.amount_to_precision(settings.symbol, settings.order_size)

    await exchange.create_market_buy_order(
        symbol=settings.symbol,
        amount=order_size,
        params={'take_profit': tp, 'stop_loss': st})

    if settings.enable_scale_orders:

        await create_scale_orders(last_price)

# Load historical data to use for the indicators.
async def load_ohlcv_data():

    today_start = current_utc_ms()

    if settings.days_to_load != 0:

        today_start - (settings.days_to_load * 86400000)

    timestamp = exchange.milliseconds()

    tasks = []

    while today_start < timestamp:

        tasks.append(exchange.fetch_ohlcv(
            settings.symbol, settings.timeframe, limit=200, since=today_start))
        
        today_start += 12000000 # 60000 (1 minute in ms) * 200 (limit)

    tasks.append(exchange.fetch_ohlcv(
        settings.symbol, settings.timeframe, limit=200, since=today_start))

    get = await asyncio.gather(*tasks)

    for i in get:

        settings.ohlcv_data.extend(i)

async def run():

    # Initialize
    await exchange.load_markets()
    await load_ohlcv_data()

    while True:
        
        try:

            result = await asyncio.gather(
                exchange.fetch_ohlcv(settings.symbol, settings.timeframe, limit=1),
                exchange.fetch_positions([settings.symbol]))

            last_price = result[0][0][4]
            position_size = result[1][0]['info']['size']

            # Update ohlcv data.
            if result[0][0][0] != settings.ohlcv_data[-1][0]:

                settings.ohlcv_data.extend(result[0])

            if position_size == '0' and not settings.cooldown:

                if settings.in_position and settings.cancel_all_orders_on_close:

                    await exchange.cancel_all_orders(settings.symbol)
                    settings.in_position = False

                # Calculate indicator
                calculate_sma = pd.DataFrame(
                    settings.ohlcv_data, 
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                    ).ta.sma(length=10).iloc[-1]

                print(last_price - calculate_sma, end='\r')

                # Checks if price is not below the inidcator.
                if settings.lock:

                    if last_price > calculate_sma + calculate_percent(
                        settings.lock_offset, calculate_sma):

                        settings.lock = False

                else:

                    if last_price < calculate_sma:

                        await create_market_order(last_price)

                        if settings.use_cooldown:

                            settings.cooldown = True
                            asyncio.create_task(cooldown_pos())

                        settings.lock = True    

            else:

                print(f"P&L: {result[1][0]['info']['unrealised_pnl']}", end='\r')

        except Exception as loop_exception:

            exception(loop_exception, exc_info=True)

# Setup async events.
async_loop = asyncio.new_event_loop()
async_loop.create_task(run())
async_loop.run_forever()
