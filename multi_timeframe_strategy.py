 """
多时间周期策略 - 演示如何处理不同周期的K线数据
"""

from time import sleep
from logging import INFO
from decimal import Decimal

from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine

from howtrader.gateway.okx import OkxGateway
from howtrader.app.cta_strategy import CtaStrategyApp, CtaTemplate, CtaEngine
from howtrader.trader.object import TickData, BarData
from howtrader.trader.utility import ArrayManager, BarGenerator
from howtrader.trader.constant import Direction, Offset, Interval

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

class MultiTimeframeStrategy(CtaTemplate):
    """
    多时间周期策略演示
    
    🕐 时间周期处理方案：
    - 基础数据：1分钟K线（HowTrader默认推送）
    - 1小时K线：使用BarGenerator生成
    - 4小时K线：使用BarGenerator生成
    - 日K线：使用BarGenerator生成
    """
    
    author = "多周期策略系统"
    
    # 策略参数
    rsi_length = 14
    fast_ma = 20
    slow_ma = 60
    trade_size = Decimal("0.001")
    
    # 多周期状态变量
    # 1小时数据
    hourly_rsi = 0.0
    hourly_fast_ma = 0.0
    hourly_slow_ma = 0.0
    
    # 4小时数据  
    four_hour_rsi = 0.0
    four_hour_trend = ""
    
    # 日线数据
    daily_trend = ""
    daily_volume_avg = 0.0
    
    parameters = ["rsi_length", "fast_ma", "slow_ma", "trade_size"]
    variables = ["hourly_rsi", "hourly_fast_ma", "hourly_slow_ma", 
                "four_hour_rsi", "four_hour_trend", "daily_trend"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # 🔑 关键：BarGenerator用于生成不同周期的K线
        
        # 1小时K线生成器
        self.bg_hour = BarGenerator(
            on_bar=self.on_bar,           # 接收1分钟K线
            window=60,                    # 60分钟 = 1小时
            on_window_bar=self.on_hour_bar,  # 1小时K线回调
            interval=Interval.MINUTE      # 基础单位是分钟
        )
        
        # 4小时K线生成器
        self.bg_4hour = BarGenerator(
            on_bar=self.on_bar,           # 接收1分钟K线  
            window=4,                     # 4个单位
            on_window_bar=self.on_4hour_bar, # 4小时K线回调
            interval=Interval.HOUR        # 基础单位是小时
        )
        
        # 日K线生成器
        self.bg_daily = BarGenerator(
            on_bar=self.on_bar,           # 接收1分钟K线
            window=1,                     # 1个单位
            on_window_bar=self.on_daily_bar, # 日K线回调
            interval=Interval.DAILY       # 基础单位是天
        )
        
        # 各周期的ArrayManager
        self.am_1min = ArrayManager(size=200)    # 1分钟数据管理器
        self.am_hour = ArrayManager(size=100)    # 1小时数据管理器  
        self.am_4hour = ArrayManager(size=50)    # 4小时数据管理器
        self.am_daily = ArrayManager(size=30)    # 日线数据管理器
        
    def on_init(self):
        """策略初始化"""
        self.write_log("🕐 多时间周期策略初始化")
        self.write_log(f"📊 交易品种: {self.vt_symbol}")
        
        # 加载历史数据预热指标
        self.write_log("📈 加载历史数据...")
        self.load_bar(10)  # 加载10天1分钟数据
        
        self.write_log("✅ 多时间周期策略初始化完成")
        
    def on_start(self):
        """策略启动"""
        self.write_log("🚀 多时间周期策略启动")
        
    def on_stop(self):
        """策略停止"""
        self.write_log("🛑 多时间周期策略停止")
        
    def on_tick(self, tick: TickData):
        """处理Tick数据"""
        super().on_tick(tick)
        
    def on_bar(self, bar: BarData):
        """
        处理1分钟K线 - 多时间周期的核心处理逻辑
        
        🔄 数据流向：
        1分钟K线 → BarGenerator → 生成更大周期K线
        """
        
        # 更新1分钟数据管理器
        self.am_1min.update_bar(bar)
        
        # 🔑 关键：将1分钟K线输入到各个BarGenerator
        # 这会自动触发相应的回调函数
        self.bg_hour.update_bar(bar)    # 可能触发on_hour_bar
        self.bg_4hour.update_bar(bar)   # 可能触发on_4hour_bar  
        self.bg_daily.update_bar(bar)   # 可能触发on_daily_bar
        
        # 基于1分钟数据的快速判断（如果需要）
        if self.am_1min.inited:
            current_rsi = self.am_1min.rsi(14)
            if current_rsi > 80 or current_rsi < 20:
                self.write_log(f"⚡ 1分钟RSI极值: {current_rsi:.2f}")
        
        self.put_event()
        
    def on_hour_bar(self, bar: BarData):
        """
        处理1小时K线 - 中期趋势分析
        """
        self.write_log(f"🕐 1小时K线: {bar.datetime.strftime('%m-%d %H:%M')} 收盘价: {bar.close_price}")
        
        # 更新1小时数据管理器
        self.am_hour.update_bar(bar)
        
        if not self.am_hour.inited:
            return
            
        # 计算1小时技术指标
        self.hourly_rsi = self.am_hour.rsi(self.rsi_length)
        self.hourly_fast_ma = self.am_hour.sma(self.fast_ma)
        self.hourly_slow_ma = self.am_hour.sma(self.slow_ma)
        
        self.write_log(f"📊 1小时指标: RSI={self.hourly_rsi:.2f}, 快MA={self.hourly_fast_ma:.2f}, 慢MA={self.hourly_slow_ma:.2f}")
        
        # 1小时级别的交易信号
        self._check_hourly_signals(bar)
        
    def on_4hour_bar(self, bar: BarData):
        """
        处理4小时K线 - 主要趋势判断
        """
        self.write_log(f"🕐 4小时K线: {bar.datetime.strftime('%m-%d %H:%M')} 收盘价: {bar.close_price}")
        
        # 更新4小时数据管理器
        self.am_4hour.update_bar(bar)
        
        if not self.am_4hour.inited:
            return
            
        # 计算4小时技术指标
        self.four_hour_rsi = self.am_4hour.rsi(self.rsi_length)
        four_hour_ma20 = self.am_4hour.sma(20)
        four_hour_ma60 = self.am_4hour.sma(60)
        
        # 判断4小时趋势
        if four_hour_ma20 > four_hour_ma60:
            self.four_hour_trend = "上升"
        elif four_hour_ma20 < four_hour_ma60:
            self.four_hour_trend = "下降"
        else:
            self.four_hour_trend = "横盘"
            
        self.write_log(f"📈 4小时分析: RSI={self.four_hour_rsi:.2f}, 趋势={self.four_hour_trend}")
        
        # 4小时级别的策略逻辑
        self._check_4hour_signals(bar)
        
    def on_daily_bar(self, bar: BarData):
        """
        处理日K线 - 长期趋势和风控
        """
        self.write_log(f"🕐 日K线: {bar.datetime.strftime('%Y-%m-%d')} 收盘价: {bar.close_price}")
        
        # 更新日线数据管理器
        self.am_daily.update_bar(bar)
        
        if not self.am_daily.inited:
            return
            
        # 计算日线指标
        daily_ma10 = self.am_daily.sma(10)
        daily_ma30 = self.am_daily.sma(30)
        self.daily_volume_avg = self.am_daily.sma_volume(20)
        
        # 判断日线趋势
        if daily_ma10 > daily_ma30:
            self.daily_trend = "多头"
        else:
            self.daily_trend = "空头"
            
        self.write_log(f"📊 日线分析: MA10={daily_ma10:.2f}, MA30={daily_ma30:.2f}, 趋势={self.daily_trend}")
        
        # 日线级别的风控检查
        self._check_daily_risk(bar)
        
    def _check_hourly_signals(self, bar: BarData):
        """1小时级别的交易信号检查"""
        
        # 多时间周期确认的买入信号
        if (self.hourly_rsi < 30 and                    # 1小时超卖
            self.hourly_fast_ma > self.hourly_slow_ma and  # 1小时均线多头
            self.four_hour_trend == "上升" and            # 4小时上升趋势
            self.daily_trend == "多头" and               # 日线多头趋势
            self.pos == 0):                             # 无持仓
            
            self.write_log("🟢 多周期买入信号确认!")
            self.write_log(f"   1小时: RSI={self.hourly_rsi:.2f} < 30")
            self.write_log(f"   4小时: 趋势={self.four_hour_trend}")
            self.write_log(f"   日线: 趋势={self.daily_trend}")
            
            # 执行买入
            buy_price = Decimal(str(bar.close_price * 1.001))
            self.buy(buy_price, self.trade_size)
            
        # 多时间周期确认的卖出信号
        elif (self.hourly_rsi > 70 and                   # 1小时超买
              self.pos > 0):                            # 有多头持仓
              
            self.write_log("🔴 多周期卖出信号确认!")
            
            # 执行卖出
            sell_price = Decimal(str(bar.close_price * 0.999))
            sell_volume = Decimal(str(abs(self.pos)))
            self.sell(sell_price, sell_volume)
            
    def _check_4hour_signals(self, bar: BarData):
        """4小时级别的策略逻辑"""
        
        # 4小时级别的趋势变化警告
        if self.four_hour_rsi > 80:
            self.write_log("⚠️ 4小时RSI严重超买，注意风险")
        elif self.four_hour_rsi < 20:
            self.write_log("💡 4小时RSI严重超卖，关注机会")
            
    def _check_daily_risk(self, bar: BarData):
        """日线级别的风控检查"""
        
        # 日线级别的风险管理
        if self.daily_trend == "空头" and self.pos > 0:
            self.write_log("⚠️ 日线转空头，考虑减仓")
            
        # 成交量异常检查
        if bar.volume > self.daily_volume_avg * 3:
            self.write_log(f"📢 异常放量: 当前={bar.volume:.0f}, 平均={self.daily_volume_avg:.0f}")

def main():
    """运行多时间周期策略"""
    print("🕐 启动多时间周期策略")
    print("=" * 50)
    
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(OkxGateway)
    cta_engine = main_engine.add_app(CtaStrategyApp)
    
    print("🔗 连接交易所...")
    main_engine.connect(OKX_SETTING, "OKX")
    sleep(10)
    
    print("📊 初始化策略...")
    if hasattr(cta_engine, 'init_engine'):
        cta_engine.init_engine()
    
    if hasattr(cta_engine, 'classes'):
        cta_engine.classes["MultiTimeframeStrategy"] = MultiTimeframeStrategy
    
    if hasattr(cta_engine, 'add_strategy'):
        cta_engine.add_strategy(
            class_name="MultiTimeframeStrategy",
            strategy_name="多周期策略", 
            vt_symbol="BTC-USDT.OKX",
            setting={}
        )
    
    print("🎯 初始化策略...")
    if hasattr(cta_engine, 'init_all_strategies'):
        cta_engine.init_all_strategies()
    sleep(30)
    
    print("✅ 启动策略...")
    if hasattr(cta_engine, 'start_all_strategies'):
        cta_engine.start_all_strategies()
    
    print("\n🎉 多时间周期策略已启动！")
    print("🕐 时间周期:")
    print("   - 1分钟: 实时数据接收")
    print("   - 1小时: 中期趋势分析")  
    print("   - 4小时: 主要趋势判断")
    print("   - 日线: 长期趋势和风控")
    
    try:
        while True:
            sleep(10)
    except KeyboardInterrupt:
        print("\n🛑 停止策略...")
        if hasattr(cta_engine, 'stop_all_strategies'):
            cta_engine.stop_all_strategies()
        sleep(3)
        main_engine.close()
        print("✅ 已安全停止")

if __name__ == "__main__":
    main()