"""
健壮版本的HowTrader策略 - 能够处理历史数据缺失
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
from howtrader.trader.utility import ArrayManager
from howtrader.trader.constant import Direction, Offset

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

class RobustStrategy(CtaTemplate):
    """
    健壮策略：能够处理历史数据缺失的情况
    """
    
    author = "健壮量化系统"
    
    # 策略参数
    rsi_length = 14        
    rsi_buy_threshold = 30  
    rsi_sell_threshold = 70  
    trade_size = Decimal("0.001")
    
    # 健壮性参数
    min_bars_for_trading = 20  # 最少需要多少根K线才开始交易
    max_position = Decimal("0.01")  # 最大持仓限制
    
    # 策略状态变量
    rsi_value = 0.0
    current_price = 0.0
    last_action = ""
    bars_received = 0  # 已接收的K线数量
    history_data_loaded = False  # 历史数据是否成功加载
    
    parameters = ["rsi_length", "rsi_buy_threshold", "rsi_sell_threshold", "trade_size", "min_bars_for_trading"]
    variables = ["rsi_value", "current_price", "last_action", "bars_received", "history_data_loaded"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        self.am = ArrayManager(size=100)  # 增大缓存
        
    def on_init(self):
        """策略初始化 - 健壮版本"""
        self.write_log("🚀 健壮策略初始化开始")
        self.write_log(f"📊 交易品种: {self.vt_symbol}")
        
        # 尝试加载历史数据
        try:
            self.write_log("📈 尝试加载历史数据...")
            bars = self.load_bar(10, use_database=False)  # 先尝试从交易所获取
            
            if bars and len(bars) > 0:
                self.write_log(f"✅ 成功从交易所加载 {len(bars)} 根历史K线")
                self.history_data_loaded = True
            else:
                self.write_log("⚠️ 交易所历史数据获取失败，尝试从数据库加载...")
                bars = self.load_bar(10, use_database=True)  # 再尝试从数据库
                
                if bars and len(bars) > 0:
                    self.write_log(f"✅ 成功从数据库加载 {len(bars)} 根历史K线")
                    self.history_data_loaded = True
                else:
                    self.write_log("⚠️ 历史数据完全无法获取，策略将使用实时数据流模式")
                    self.history_data_loaded = False
                    
        except Exception as e:
            self.write_log(f"❌ 历史数据加载异常: {e}")
            self.history_data_loaded = False
        
        # 策略状态总结
        if self.history_data_loaded:
            self.write_log("🎯 策略模式: 历史数据 + 实时数据")
        else:
            self.write_log("🎯 策略模式: 纯实时数据流（需要等待足够数据）")
            self.write_log(f"⏳ 需要接收至少 {self.min_bars_for_trading} 根K线才开始交易")
        
    def on_start(self):
        """策略启动"""
        self.write_log("✅ 健壮策略启动 - 开始监控")
        
    def on_stop(self):
        """策略停止"""
        self.write_log("🛑 健壮策略停止")
        
    def on_tick(self, tick: TickData):
        """处理实时行情数据"""
        self.current_price = float(tick.last_price)
        super().on_tick(tick)
        
    def on_bar(self, bar: BarData):
        """处理K线数据 - 健壮版本"""
        
        # 更新计数器
        self.bars_received += 1
        
        self.write_log(f"📊 新K线[{self.bars_received}]: {bar.datetime.strftime('%H:%M:%S')} 价格: {bar.close_price}")
        
        # 更新ArrayManager
        am = self.am
        am.update_bar(bar)
        
        # 检查是否可以开始交易
        if not self._can_start_trading():
            return
            
        # 计算RSI
        self.rsi_value = am.rsi(self.rsi_length)
        self.write_log(f"🔢 当前RSI: {self.rsi_value:.2f}")
        
        # 执行交易逻辑
        self._check_trading_signals(bar)
        
        # 更新界面
        self.put_event()
        
    def _can_start_trading(self) -> bool:
        """检查是否可以开始交易"""
        
        # 如果成功加载了历史数据，且ArrayManager已初始化
        if self.history_data_loaded and self.am.inited:
            return True
            
        # 如果没有历史数据，需要等待足够的实时数据
        if not self.history_data_loaded:
            if self.bars_received < self.min_bars_for_trading:
                if self.bars_received % 5 == 0:  # 每5根K线提醒一次
                    remaining = self.min_bars_for_trading - self.bars_received
                    self.write_log(f"⏳ 实时数据积累中...还需 {remaining} 根K线")
                return False
                
            if not self.am.inited:
                self.write_log("⏳ 等待技术指标初始化...")
                return False
                
            # 达到条件，可以开始交易
            self.write_log("🎉 实时数据已足够，开始交易！")
            return True
            
        return False
        
    def _check_trading_signals(self, bar: BarData):
        """检查交易信号 - 增加风控"""
        
        current_pos = self.pos
        price = bar.close_price
        
        # 风控检查
        if abs(current_pos) >= self.max_position:
            self.write_log(f"⚠️ 已达最大持仓限制: {current_pos}")
            return
            
        # 买入信号
        if (self.rsi_value < self.rsi_buy_threshold and 
            current_pos == 0 and
            self.rsi_value > 0):  # 确保RSI有效
            
            self.write_log(f"🟢 买入信号! RSI={self.rsi_value:.2f}")
            
            # 计算买入价格（市价+小幅上浮确保成交）
            buy_price = Decimal(str(price * 1.001))  # 上浮0.1%
            
            self.buy(buy_price, self.trade_size)
            self.last_action = f"买入 {self.trade_size} BTC @ {buy_price}"
            
        # 卖出信号
        elif (self.rsi_value > self.rsi_sell_threshold and 
              current_pos > 0 and
              self.rsi_value > 0):  # 确保RSI有效
              
            self.write_log(f"🔴 卖出信号! RSI={self.rsi_value:.2f}")
            
            # 计算卖出价格（市价-小幅下调确保成交）
            sell_price = Decimal(str(price * 0.999))  # 下调0.1%
            sell_volume = Decimal(str(abs(self.pos)))
            
            self.sell(sell_price, sell_volume)
            self.last_action = f"卖出 {sell_volume} BTC @ {sell_price}"
            
    def on_order(self, order):
        """订单回报"""
        self.write_log(f"📋 订单: {order.symbol} {order.direction.value} {order.volume} @ {order.price} [{order.status.value}]")
        
    def on_trade(self, trade):
        """成交回报"""
        self.write_log(f"✅ 成交: {trade.symbol} {trade.direction.value} {trade.volume} @ {trade.price}")
        self.write_log(f"💰 持仓: {self.pos}")
        
        # 成交后的状态检查
        if abs(self.pos) > self.max_position:
            self.write_log(f"⚠️ 警告：持仓超过限制！当前: {self.pos}, 限制: {self.max_position}")

def main():
    """主程序"""
    print("🚀 启动健壮版HowTrader策略")
    print("💪 特点：能够处理历史数据缺失的情况")
    print("=" * 50)
    
    # 引擎初始化
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(OkxGateway)
    cta_engine = main_engine.add_app(CtaStrategyApp)
    
    # 连接交易所
    print("🔗 连接 OKX...")
    main_engine.connect(OKX_SETTING, "OKX")
    sleep(10)
    
    # 初始化策略引擎
    print("📊 初始化策略引擎...")
    if hasattr(cta_engine, 'init_engine'):
        cta_engine.init_engine()
    
    if hasattr(cta_engine, 'classes'):
        cta_engine.classes["RobustStrategy"] = RobustStrategy
    
    # 创建策略
    if hasattr(cta_engine, 'add_strategy'):
        cta_engine.add_strategy(
            class_name="RobustStrategy",
            strategy_name="健壮策略", 
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
    
    print("\n🎉 健壮策略已启动！")
    print("💡 特性:")
    print("   - 自动处理历史数据缺失")
    print("   - 增强风控机制")
    print("   - 实时数据流备用方案")
    
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