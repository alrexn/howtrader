"""
HowTrader 实战学习 Demo
边做边学：从行情获取到策略执行的完整流程
"""

from time import sleep
from logging import INFO
from datetime import datetime

from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine

# 导入交易所网关
from howtrader.gateway.binance import BinanceUsdtGateway
from howtrader.gateway.okx import OkxGateway

# 导入策略相关
from howtrader.app.cta_strategy import CtaStrategyApp, CtaTemplate
from howtrader.trader.object import TickData, BarData
from howtrader.trader.utility import ArrayManager
from howtrader.trader.constant import Direction, Offset
from decimal import Decimal

# ===============================
# 📊 第一步：创建一个简单策略
# ===============================

class LearningStrategy(CtaTemplate):
    """
    学习用的简单策略 - 展示HowTrader核心功能
    """
    
    author = "HowTrader学习者"
    
    # 策略参数
    rsi_length = 14
    rsi_buy_line = 30
    rsi_sell_line = 70
    
    # 策略变量（会显示在界面上）
    rsi_value = 0.0
    current_price = 0.0
    
    parameters = ["rsi_length", "rsi_buy_line", "rsi_sell_line"]
    variables = ["rsi_value", "current_price"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        self.am = ArrayManager()
        self.last_tick_time = None
        
    def on_init(self):
        """策略初始化"""
        self.write_log("策略初始化开始")
        self.load_bar(10)  # 加载10天历史数据
        
    def on_start(self):
        """策略启动"""
        self.write_log("策略启动 - 开始监控市场")
        
    def on_stop(self):
        """策略停止"""
        self.write_log("策略停止")
        
    def on_tick(self, tick: TickData):
        """
        🎯 核心1：接收实时行情数据
        这里展示如何处理实时tick数据
        """
        self.current_price = tick.last_price
        
        # 只在新的分钟显示tick信息，避免输出过多
        current_minute = tick.datetime.strftime("%H:%M")
        if not self.last_tick_time or self.last_tick_time != current_minute:
            self.write_log(f"📡 收到实时行情: {tick.symbol} 价格:{tick.last_price}")
            self.last_tick_time = current_minute
            
        super().on_tick(tick)
        
    def on_bar(self, bar: BarData):
        """
        🎯 核心2：K线数据处理和信号生成
        """
        self.write_log(f"📊 新K线: {bar.datetime} 价格:{bar.close_price}")
        
        # 更新ArrayManager
        am = self.am
        am.update_bar(bar)
        
        if not am.inited:
            return
            
        # 计算RSI指标
        self.rsi_value = am.rsi(self.rsi_length)
        
        self.write_log(f"🔢 当前RSI: {self.rsi_value:.2f}")
        
        # 🎯 核心3：交易信号生成
        if self.pos == 0:  # 无持仓
            if self.rsi_value < self.rsi_buy_line:
                self.write_log(f"🟢 买入信号! RSI({self.rsi_value:.2f}) < {self.rsi_buy_line}")
                # 这里会下单买入
                self.buy(Decimal(str(bar.close_price + 5)), Decimal("0.01"))  # 买入0.01个币
                
        elif self.pos > 0:  # 有多头持仓
            if self.rsi_value > self.rsi_sell_line:
                self.write_log(f"🔴 卖出信号! RSI({self.rsi_value:.2f}) > {self.rsi_sell_line}")
                # 这里会下单卖出
                self.sell(Decimal(str(bar.close_price - 5)), Decimal(str(abs(self.pos))))
        
        # 更新界面显示
        self.put_event()


# ===============================
# 🚀 主程序：HowTrader启动流程
# ===============================

def main():
    """
    HowTrader 完整启动流程演示
    """
    
    print("=" * 50)
    print("🎯 HowTrader 实战学习开始!")
    print("=" * 50)
    
    # 📝 第一步：配置日志
    SETTINGS["log.active"] = True
    SETTINGS["log.level"] = INFO
    SETTINGS["log.console"] = True
    SETTINGS["log.file"] = True
    
    # 🏗️ 第二步：创建核心引擎
    print("\n🏗️ 步骤1: 创建核心引擎")
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 🔌 第三步：添加交易所网关
    print("🔌 步骤2: 添加交易所网关")
    main_engine.add_gateway(BinanceUsdtGateway)  # 币安U本位合约
    main_engine.add_gateway(OkxGateway)          # OKX交易所
    
    # 📊 第四步：添加策略应用
    print("📊 步骤3: 添加CTA策略应用")
    cta_engine = main_engine.add_app(CtaStrategyApp)
    
    print("\n⚠️  接下来需要配置交易所API密钥...")
    print("💡 学习提示：")
    print("   1. 去交易所申请API密钥")
    print("   2. 配置网关连接参数")
    print("   3. 添加策略到引擎")
    print("   4. 启动策略开始交易")
    
    # 🔑 配置示例（需要真实的API密钥）
    gateway_setting = {
        "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",         # 已经添加OKX的API密钥
        "secret": "37C1783FB06567FE998CE1FC97FC242A",   # 已经添加OKX的API密钥
        "passphrase": "Qxat240925.",  # OKX需要passphrase
        "server": "REAL",                # 选择"REAL"实盘或"TEST"测试环境
        "proxy_host": "",
        "proxy_port": 0,
    }
    
    print(f"\n🔧 当前网关配置: {list(gateway_setting.keys())}")
    
    # 如果有真实密钥，取消下面的注释来连接交易所
    """
    # 🔗 第五步：连接交易所
    print("🔗 步骤4: 连接交易所...")
    main_engine.connect(gateway_setting, "BINANCE_USDT")
    sleep(10)  # 等待连接建立
    
    # 📈 第六步：初始化策略引擎
    print("📈 步骤5: 初始化策略引擎")
    cta_engine.init_engine()
    
    # 🎯 第七步：添加策略
    print("🎯 步骤6: 添加学习策略")
    cta_engine.add_strategy(
        class_name="LearningStrategy",
        strategy_name="我的学习策略",
        vt_symbol="BTCUSDT.BINANCE_USDT",  # 交易BTC/USDT
        setting={}
    )
    
    # 🚀 第八步：启动策略
    print("🚀 步骤7: 启动策略")
    cta_engine.init_all_strategies()
    sleep(30)  # 等待策略初始化
    cta_engine.start_all_strategies()
    
    print("\n✅ 策略已启动! 开始监控...")
    
    # 💻 第九步：监控循环
    try:
        while True:
            sleep(10)
            # 这里可以添加监控逻辑
            print(f"⏰ {datetime.now().strftime('%H:%M:%S')} - 策略运行中...")
            
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号，正在关闭...")
        cta_engine.stop_all_strategies()
        main_engine.close()
    """
    
    print("\n📚 学习总结：")
    print("✅ 了解了 EventEngine + MainEngine 架构")
    print("✅ 学会了添加交易所网关")
    print("✅ 掌握了 CTA策略应用")
    print("✅ 创建了自己的策略类")
    print("\n🎯 下一步：配置真实API密钥，开始实盘测试!")


if __name__ == "__main__":
    main() 