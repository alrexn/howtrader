from howtrader.app.cta_strategy.backtesting import BacktestingEngine, OptimizationSetting
from howtrader.trader.object import Interval
from datetime import datetime
from strategies.atr_rsi_strategy import AtrRsiStrategy  # 要导入你回测的策略，你自己开发的。
from strategies.atr_rsi_15min_strategy import AtrRsi15MinStrategy

engine = BacktestingEngine()
engine.set_parameters(
    vt_symbol="BTCUSDT.OKX",
    interval=Interval.MINUTE_5,  # 改为5分钟间隔，匹配你的数据
    start=datetime(2023, 1, 1),
    end=datetime(2023, 6, 1),
    rate=0.0002,
    slippage=0.01,
    size=1,
    pricetick=0.01,
    capital=1000000,
)

# engine.add_strategy(AtrRsiStrategy, {})
engine.add_strategy(AtrRsi15MinStrategy, {})

engine.load_data()
engine.run_backtesting()
df = engine.calculate_result()
engine.calculate_statistics()
engine.show_chart()

setting = OptimizationSetting()
setting.set_target("sharpe_ratio")
setting.add_parameter("atr_length", 3, 39, 1)
setting.add_parameter("atr_ma_length", 10, 30, 1)

result = engine.run_ga_optimization(setting)  # 优化策略参数
print(result)  # 打印回测的结果，结果中会有比较好的结果值。
