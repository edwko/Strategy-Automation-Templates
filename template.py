import asyncio
import datetime
from dataclasses import dataclass
import ccxt.pro as ccxt
import pandas as pd
import pandas_ta as ta

def current_utc_ms():

    return datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
        ).timestamp() * 1000

def calculate_percent(percent, price):

    return percent * (price / 100)

@dataclass
class settings:

    symbol = "BTC/USDT:USDT"
    timeframe = "1m"

    order_size = 0.001
    
    take_profit_percent = 1.0
    stop_loss_percent = 1.0

    ohlcv_data = []

    # If the number of days to load is set to zero,
    # the data will be loaded from the start of the current day.
    days_to_load = 0

    api_key = ''
    api_secret = ''

    use_cooldown = False
    cooldown_timer = 60
    cooldown = False

    init_last_pos = True
    init_offset = 20

    position_size = 0

# Setup exchange and API connection
exchange = ccxt.bybit({
    'apiKey': settings.api_key,
    'secret': settings.api_secret,
    'enableRateLimit': True})

# Cooldown after position is entered.
async def cooldown_pos():

    await asyncio.sleep(settings.cooldown_timer)
    settings.cooldown = False

# Create market order with take profit and stop loss.
async def create_market_order(last_price):

    tp = exchange.price_to_precision(
        settings.symbol, 
        last_price + calculate_percent(settings.take_profit_percent, last_price))

    st = exchange.price_to_precision(
        settings.symbol, 
        last_price - calculate_percent(settings.stop_loss_percent, last_price))

    order_size = exchange.amount_to_precision(
        settings.symbol, settings.order_size)

    await exchange.create_market_buy_order(
        symbol=settings.symbol,
        amount=order_size,
        params={
            'take_profit': tp,
            'stop_loss': st
        }
    )

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

async def get_position():

    while True:

        pos = await exchange.fetch_positions([settings.symbol])
        settings.position_size = pos[0]['size']

        if settings.position_size != 0:

            print(f"P&L: {pos[0]['info']['unrealised_pnl']}", end='\r')

async def run():

    await load_ohlcv_data()

    while True:

        get_ohlcv = await exchange.watch_ohlcv(
            settings.symbol, settings.timeframe, limit=1)

        last_price = get_ohlcv[0][4]

        # Update ohlcv data.
        if get_ohlcv[0][0] != settings.ohlcv_data[-1][0]:

            settings.ohlcv_data.extend(get_ohlcv)

        # Calculate indicator
        calculate_sma = pd.DataFrame(
            settings.ohlcv_data, 
            columns=["timestamp", "open", "high", "low", "close", "volume"]
            ).ta.sma(length=10).iloc[-1]
 
        # Checks if price is not below the inidcator.
        if settings.init_last_pos:

            if last_price > calculate_sma + settings.init_offset:

                settings.init_last_pos = False
        
        if not settings.init_last_pos and \
            not settings.cooldown and settings.position_size == 0:
           
            if last_price < calculate_sma:

                await create_market_order(last_price)

                if settings.use_cooldown:

                    settings.cooldown = True
                    asyncio.create_task(cooldown_pos())

                settings.init_last_pos = True

        if settings.position_size == 0:

            print(last_price - calculate_sma, end='\r')

# Setup async events.
async_loop = asyncio.new_event_loop()
async_loop.create_task(run())
async_loop.create_task(get_position())
async_loop.run_forever()
