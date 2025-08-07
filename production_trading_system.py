"""
HowTrader 生产级量化交易系统完整示例
演示：数据获取 -> 信号生成 -> 订单执行 -> 监控管理
"""

import sys
from time import sleep
from datetime import datetime
from logging import INFO
from decimal import Decimal

from howtrader.event import EventEngine, Event
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine, LogEngine
from howtrader.trader.object import TickData, BarData, TradeData, OrderData, PositionData
from howtrader.trader.constant import Direction, Offset
from howtrader.trader.utility import ArrayManager

# 导入交易所网关
from howtrader.gateway.okx import OkxGateway
from howtrader.gateway.binance import BinanceUsdtGateway

# 导入策略应用
from howtrader.app.cta_strategy import CtaStrategyApp, CtaEngine, CtaTemplate
from howtrader.app.cta_strategy.base import EVENT_CTA_LOG

# ===============================
# 1. 系统配置
# ===============================

SETTINGS["log.active"] = True
SETTINGS["log.level"] = INFO
SETTINGS["log.console"] = True
SETTINGS["log.file"] = True

# 交易所配置 - 请填入你的API密钥
OKX_GATEWAY_SETTING = {
    "key": "your_api_key",
    "secret": "your_secret_key", 
    "passphrase": "your_passphrase",
    "proxy_host": "",  # 如果需要代理
    "proxy_port": 0,
    "server": "REAL"  # "REAL" 或 "TEST"
}

# ===============================
# 2. 自定义量化策略
# ===============================

class ProductionStrategy(CtaTemplate):
    """
    生产级量化策略示例
    功能：基于RSI和移动平均线的双重确认策略
    """
    
    author = "HowTrader Production"
    
    # 策略参数
    rsi_length = 14
    rsi_overbought = 70
    rsi_oversold = 30
    ma_fast = 10
    ma_slow = 20
    trade_size = 0.1
    max_position = 1.0
    
    # 策略变量
    rsi_value = 0.0
    ma_fast_value = 0.0
    ma_slow_value = 0.0
    current_price = 0.0
    
    parameters = [
        "rsi_length", "rsi_overbought", "rsi_oversold", 
        "ma_fast", "ma_slow", "trade_size", "max_position"
    ]
    variables = [
        "rsi_value", "ma_fast_value", "ma_slow_value", "current_price"
    ]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # 初始化技术分析工具
        self.am = ArrayManager(size=100)
        
        # 信号状态
        self.signal_long = False
        self.signal_short = False
        
        # 风控参数
        self.last_order_time = None
        self.min_order_interval = 60  # 最小下单间隔(秒)
        
    def on_init(self):
        """策略初始化"""
        self.write_log("=== 生产策略初始化开始 ===")
        self.write_log(f"交易品种: {self.vt_symbol}")
        self.write_log(f"策略参数: RSI({self.rsi_length}), MA({self.ma_fast}/{self.ma_slow})")
        
        # 加载历史数据用于指标计算
        self.load_bar(30)  # 加载30天历史数据
        
    def on_start(self):
        """策略启动"""
        self.write_log("=== 策略启动，开始实盘交易 ===")
        
    def on_stop(self):
        """策略停止"""
        self.write_log("=== 策略停止，清理所有订单 ===")
        self.cancel_all()
        
    def on_tick(self, tick: TickData):
        """实时行情数据处理"""
        self.current_price = tick.last_price
        
        # 更新K线数据（这里使用1分钟K线）
        # 注意：在实际应用中可能需要BarGenerator来生成不同周期的K线
        
    def on_bar(self, bar: BarData):
        """K线数据处理 - 核心策略逻辑"""
        
        # 更新数组管理器
        self.am.update_bar(bar)
        if not self.am.inited:
            return
            
        # === 数据获取部分 ===
        # 计算技术指标
        self.rsi_value = self.am.rsi(self.rsi_length)
        self.ma_fast_value = self.am.sma(self.ma_fast)
        self.ma_slow_value = self.am.sma(self.ma_slow)
        
        # 记录关键数据
        self.write_log(
            f"市场数据 | 价格: {bar.close_price:.2f} | "
            f"RSI: {self.rsi_value:.2f} | "
            f"MA快线: {self.ma_fast_value:.2f} | "
            f"MA慢线: {self.ma_slow_value:.2f} | "
            f"当前持仓: {self.pos}"
        )
        
        # === 信号生成部分 ===
        self.generate_trading_signals(bar)
        
        # === 订单执行部分 ===
        self.execute_trading_logic(bar)
        
    def generate_trading_signals(self, bar: BarData):
        """生成交易信号"""
        
        # 重置信号
        self.signal_long = False
        self.signal_short = False
        
        # 多头信号：RSI超卖 + 快线上穿慢线
        if (self.rsi_value < self.rsi_oversold and 
            self.ma_fast_value > self.ma_slow_value):
            self.signal_long = True
            self.write_log(f"🔵 生成多头信号 | RSI: {self.rsi_value:.2f} | MA金叉确认")
            
        # 空头信号：RSI超买 + 快线下穿慢线  
        elif (self.rsi_value > self.rsi_overbought and 
              self.ma_fast_value < self.ma_slow_value):
            self.signal_short = True
            self.write_log(f"🔴 生成空头信号 | RSI: {self.rsi_value:.2f} | MA死叉确认")
            
    def execute_trading_logic(self, bar: BarData):
        """执行交易逻辑"""
        
        # 风控检查
        if not self.risk_check():
            return
            
        current_time = datetime.now()
        
        # 空仓时的开仓逻辑
        if self.pos == 0:
            if self.signal_long:
                price = bar.close_price * 1.001  # 稍微高于收盘价确保成交
                volume = Decimal(str(self.trade_size))
                
                orderids = self.buy(Decimal(str(price)), volume)
                self.write_log(f"📈 发送开多订单 | 价格: {price:.2f} | 数量: {volume} | 订单ID: {orderids}")
                self.last_order_time = current_time
                
            elif self.signal_short:
                price = bar.close_price * 0.999  # 稍微低于收盘价确保成交
                volume = Decimal(str(self.trade_size))
                
                orderids = self.short(Decimal(str(price)), volume)
                self.write_log(f"📉 发送开空订单 | 价格: {price:.2f} | 数量: {volume} | 订单ID: {orderids}")
                self.last_order_time = current_time
                
        # 持仓时的平仓逻辑
        elif self.pos > 0:  # 持多仓
            if self.signal_short or self.rsi_value > 75:  # 反向信号或极度超买
                orderids = self.sell(Decimal(str(bar.close_price)), Decimal(str(abs(self.pos))))
                self.write_log(f"📉 发送平多订单 | 价格: {bar.close_price:.2f} | 全部平仓")
                self.last_order_time = current_time
                
        elif self.pos < 0:  # 持空仓
            if self.signal_long or self.rsi_value < 25:  # 反向信号或极度超卖
                orderids = self.cover(Decimal(str(bar.close_price)), Decimal(str(abs(self.pos))))
                self.write_log(f"📈 发送平空订单 | 价格: {bar.close_price:.2f} | 全部平仓")
                self.last_order_time = current_time
                
    def risk_check(self) -> bool:
        """风险控制检查"""
        
        # 检查下单频率
        if self.last_order_time:
            time_diff = (datetime.now() - self.last_order_time).total_seconds()
            if time_diff < self.min_order_interval:
                return False
                
        # 检查最大持仓
        if abs(self.pos) >= self.max_position:
            self.write_log(f"⚠️ 持仓已达上限: {self.pos}")
            return False
            
        return True
        
    def on_trade(self, trade: TradeData):
        """成交回调 - 记录交易信息"""
        direction_text = "买入" if trade.direction == Direction.LONG else "卖出"
        self.write_log(
            f"✅ 交易成交 | {direction_text} | "
            f"价格: {trade.price} | 数量: {trade.volume} | "
            f"时间: {trade.datetime} | 成交ID: {trade.tradeid}"
        )
        
    def on_order(self, order: OrderData):
        """订单状态回调"""
        self.write_log(f"📋 订单状态: {order.status.value} | 订单ID: {order.orderid}")
        
    def on_position(self, position: PositionData):
        """持仓变化回调"""
        self.write_log(f"💼 持仓更新: {position.volume} | 均价: {position.price}")

# ===============================
# 3. 系统监控模块
# ===============================

class TradingSystemMonitor:
    """交易系统监控器"""
    
    def __init__(self, main_engine: MainEngine):
        self.main_engine = main_engine
        self.start_time = datetime.now()
        
    def print_system_status(self):
        """打印系统状态"""
        print("\n" + "="*60)
        print(f"🚀 HowTrader 量化交易系统运行状态")
        print(f"📅 启动时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏰ 运行时长: {datetime.now() - self.start_time}")
        
        # 获取网关状态
        gateways = self.main_engine.gateways
        print(f"🔗 已连接网关: {list(gateways.keys())}")
        
        # 获取策略状态  
        apps = self.main_engine.apps
        if "CtaStrategy" in apps:
            cta_engine = self.main_engine.engines.get("CtaStrategy")
            if cta_engine and hasattr(cta_engine, 'strategies'):
                strategies = getattr(cta_engine, 'strategies', {})
                print(f"📊 运行策略数: {len(strategies)}")
                for name, strategy in strategies.items():
                    status = "运行中" if getattr(strategy, 'trading', False) else "已停止"
                    pos = getattr(strategy, 'pos', 0)
                    print(f"   - {name}: {status} (持仓: {pos})")
        
        print("="*60 + "\n")

# ===============================
# 4. 主程序入口
# ===============================

def run_production_system():
    """运行生产级交易系统"""
    
    print("🚀 启动 HowTrader 生产级量化交易系统...")
    
    # === 1. 初始化核心引擎 ===
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # === 2. 添加交易所网关 ===
    main_engine.add_gateway(OkxGateway)
    # main_engine.add_gateway(BinanceUsdtGateway)  # 可以同时连接多个交易所
    
    # === 3. 添加策略应用 ===
    cta_engine = main_engine.add_app(CtaStrategyApp)
    
    # === 4. 设置日志系统 ===
    log_engine = main_engine.get_engine("log")
    process_log_event = getattr(log_engine, 'process_log_event', None)
    if process_log_event:
        event_engine.register(EVENT_CTA_LOG, process_log_event)
    
    print("✅ 核心组件初始化完成")
    
    # === 5. 连接交易所 ===
    print("🔗 连接交易所...")
    main_engine.connect(OKX_GATEWAY_SETTING, "OKX")
    sleep(10)  # 等待连接建立
    
    # === 6. 初始化策略引擎 ===
    print("📊 初始化策略引擎...")
    init_engine = getattr(cta_engine, 'init_engine', None)
    if init_engine:
        init_engine()
    
    # === 7. 添加策略 ===
    # 策略类会自动通过load_strategy_class加载，我们手动添加到classes
    classes = getattr(cta_engine, 'classes', None)
    if classes is not None:
        classes["ProductionStrategy"] = ProductionStrategy
    
    # 添加策略实例
    add_strategy = getattr(cta_engine, 'add_strategy', None)
    if add_strategy:
        add_strategy(
            class_name="ProductionStrategy",
            strategy_name="BTC_Production_Strategy",
            vt_symbol="BTCUSDT.OKX",
            setting={}
        )
    
    # === 8. 初始化所有策略 ===
    print("🎯 初始化策略...")
    init_all_strategies = getattr(cta_engine, 'init_all_strategies', None)
    if init_all_strategies:
        init_all_strategies()
    sleep(30)  # 等待策略初始化完成
    
    # === 9. 启动所有策略 ===
    print("🚀 启动策略交易...")
    start_all_strategies = getattr(cta_engine, 'start_all_strategies', None)
    if start_all_strategies:
        start_all_strategies()
    
    # === 10. 初始化监控器 ===
    monitor = TradingSystemMonitor(main_engine)
    
    print("🎉 系统启动完成！开始实盘交易...")
    
    # === 11. 主循环 ===
    try:
        loop_count = 0
        while True:
            sleep(60)  # 每分钟检查一次
            loop_count += 1
            
            # 每10分钟打印一次系统状态
            if loop_count % 10 == 0:
                monitor.print_system_status()
                
    except KeyboardInterrupt:
        print("\n⏹️  接收到停止信号，正在安全关闭系统...")
        
        # 停止所有策略
        stop_all_strategies = getattr(cta_engine, 'stop_all_strategies', None)
        if stop_all_strategies:
            stop_all_strategies()
        
        # 关闭主引擎
        main_engine.close()
        
        print("✅ 系统已安全关闭")
        
if __name__ == "__main__":
    print("""
    ===================================================
    🏆 HowTrader 生产级量化交易系统
    ===================================================
    
    本系统演示了完整的量化交易流程：
    
    📡 1. 数据获取：WebSocket实时行情 + REST历史数据
    🧠 2. 信号生成：基于RSI+MA的技术分析策略
    ⚡ 3. 订单执行：自动下单到交易所
    📊 4. 监控管理：实时状态监控 + 风险控制
    
    ⚠️  注意：
    - 请在 OKX_GATEWAY_SETTING 中配置你的API密钥
    - 建议先在测试环境运行
    - 实盘前请充分测试策略逻辑
    
    ===================================================
    """)
    
    # 确认后启动
    confirm = input("确认启动生产系统？(y/N): ")
    if confirm.lower() == 'y':
        run_production_system()
    else:
        print("已取消启动") 