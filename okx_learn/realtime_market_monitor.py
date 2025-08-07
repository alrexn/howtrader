"""
实时行情监控策略 - 专门用于查看行情数据流
"""

from time import sleep
from logging import INFO
from decimal import Decimal
from datetime import datetime

from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine

from howtrader.gateway.okx import OkxGateway
from howtrader.app.cta_strategy import CtaStrategyApp, CtaTemplate, CtaEngine
from howtrader.trader.object import TickData, BarData
from howtrader.trader.constant import Direction, Offset

from typing import cast

# 配置日志
SETTINGS["log.active"] = True
SETTINGS["log.level"] = INFO
SETTINGS["log.console"] = True
SETTINGS["log.file"] = True

# OKX API 配置
OKX_SETTING = {
    "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",
    "secret": "37C1783FB06567FE998CE1FC97FC242A", 
    "passphrase": "Qxat240925.",
    "proxy_host": "",
    "proxy_port": 0,
    "server": "REAL"
}

class RealtimeMarketMonitor(CtaTemplate):
    """
    实时行情监控策略：实时显示所有tick和bar数据
    """
    
    author = "行情监控系统"
    
    # 计数器
    tick_count = 0
    bar_count = 0
    
    parameters = []
    variables = ["tick_count", "bar_count"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.last_tick_time = None
        self.last_bar_time = None
        
    def on_init(self):
        """策略初始化"""
        self.write_log("🚀 实时行情监控策略初始化")
        self.write_log(f"📈 监控标的: {self.vt_symbol}")
        
        # 加载历史数据用于预热
        self.load_bar(10)  # 加载10根K线用于预热
        
    def on_start(self):
        """策略启动"""
        self.write_log("▶️  实时行情监控已启动")
        self.write_log("📡 开始接收实时行情数据...")
        
    def on_stop(self):
        """策略停止"""
        self.write_log("⏹️  实时行情监控已停止")
        self.write_log(f"📊 统计: 处理Tick={self.tick_count}个, Bar={self.bar_count}个")
        
    def on_tick(self, tick: TickData):
        """实时显示每个Tick数据"""
        self.tick_count += 1
        current_time = datetime.now()
        
        # 计算数据延迟
        delay = ""
        if self.last_tick_time:
            time_diff = (current_time - self.last_tick_time).total_seconds()
            delay = f" [间隔: {time_diff:.2f}s]"
        
        # 实时打印tick数据
        print(f"📊 TICK[{self.tick_count:04d}] {tick.datetime.strftime('%H:%M:%S.%f')[:-3]} | "
              f"价格: {tick.last_price} | 买1: {tick.bid_price_1}@{tick.bid_volume_1} | "
              f"卖1: {tick.ask_price_1}@{tick.ask_volume_1} | 成交量: {tick.volume}{delay}")
        
        self.last_tick_time = current_time
        
        # 每10个tick记录一次到日志
        if self.tick_count % 10 == 0:
            self.write_log(f"📈 已处理 {self.tick_count} 个Tick数据")
        
    def on_bar(self, bar: BarData):
        """实时显示每个Bar数据"""
        self.bar_count += 1
        current_time = datetime.now()
        
        # 计算Bar间隔
        interval = ""
        if self.last_bar_time:
            bar_diff = (bar.datetime - self.last_bar_time).total_seconds()
            interval = f" [间隔: {bar_diff:.0f}s]"
        
        # 实时打印bar数据
        print(f"📈 BAR[{self.bar_count:03d}] {bar.datetime.strftime('%H:%M:%S')} | "
              f"开: {bar.open_price} | 高: {bar.high_price} | "
              f"低: {bar.low_price} | 收: {bar.close_price} | "
              f"量: {bar.volume}{interval}")
        
        self.write_log(f"📊 K线数据[{self.bar_count}]: OHLCV=({bar.open_price}, {bar.high_price}, {bar.low_price}, {bar.close_price}, {bar.volume})")
        
        self.last_bar_time = bar.datetime

def main():
    """启动实时行情监控"""
    print("🔍 启动实时行情监控")
    print("=" * 60)
    
    # 创建引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(OkxGateway)
    cta_engine: CtaEngine = cast(CtaEngine, main_engine.add_app(CtaStrategyApp))
    
    print("🔗 连接交易所...")
    main_engine.connect(OKX_SETTING, "OKX")
    sleep(10)
    
    print("📊 初始化CTA引擎...")
    cta_engine.init_engine()
    
    # 手动注册策略类（由于不在默认目录）
    cta_engine.classes["RealtimeMarketMonitor"] = RealtimeMarketMonitor
    print("✅ 策略类注册成功")
    
    # 添加策略实例
    cta_engine.add_strategy(
        class_name="RealtimeMarketMonitor",
        strategy_name="实时行情监控", 
        vt_symbol="BTC-USDT.OKX",
        setting={}
    )
    print("✅ 策略实例创建成功")
    
    print("🎯 初始化策略...")
    cta_engine.init_all_strategies()
    sleep(10)  # 给足够时间完成初始化
    print("✅ 策略初始化完成")
    
    print("🚀 启动策略...")
    cta_engine.start_all_strategies()
    print("✅ 策略启动成功")
    
    print("\n🎉 实时行情监控已启动！")
    print("💡 监控内容:")
    print("   📊 每个Tick数据 (价格、买卖盘、成交量)")
    print("   📈 每个Bar数据 (OHLCV)")
    print("   ⏰ 数据时间和间隔")
    print("   📈 实时统计计数")
    print("\n按 Ctrl+C 停止监控...")
    
    try:
        # 持续运行直到手动停止
        while True:
            sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 停止监控...")
        
    print("🔄 正在停止策略...")
    cta_engine.stop_all_strategies()
    sleep(3)
    print("🔄 正在关闭引擎...")
    main_engine.close()
    print("✅ 监控已停止")

if __name__ == "__main__":
    main() 