#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
运行HowTrader马丁格尔策略 - 简化版
基于CTA策略脚本启动
"""

import time
from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine
from howtrader.gateway.okx import OkxGateway
from howtrader.app.cta_strategy import CtaStrategyApp

# 注册策略类
from howtrader_martingale_strategy import HowTraderMartingaleStrategy

# 配置日志
SETTINGS["log.active"] = True
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

def main():
    """主函数"""
    print("🚀 HowTrader马丁格尔策略启动")
    print("=" * 50)
    
    # 初始化引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 添加网关
    main_engine.add_gateway(OkxGateway)
    
    # 添加CTA策略应用
    cta_app: CtaStrategyApp = main_engine.add_app(CtaStrategyApp)
    
    # 连接交易所
    print("🔗 连接OKX...")
    main_engine.connect(OKX_SETTING, "OKX")
    time.sleep(5)
    print("✅ 连接完成")
    
    # 策略参数
    strategy_setting = {
        "lever": 10,
        "first_margin": 20.0,
        "first_margin_add": 20.0,
        "adding_number": 15,
        "amount_multiplie": 1.15,
        "profit_target": 0.008,
        "opp_ratio": 0.018,
        "mode": 1,  # 做多
        "enable_option_hedge": False,
    }
    
    # 手动创建和启动策略
    vt_symbol = "BTC-USDT-SWAP.OKX"
    strategy_name = "MartingaleStrategy_BTC"
    
    # 创建策略实例
    strategy = HowTraderMartingaleStrategy(
        cta_app.cta_engine,
        strategy_name, 
        vt_symbol,
        strategy_setting
    )
    
    # 注册策略到引擎
    cta_app.cta_engine.strategies[strategy_name] = strategy
    
    print(f"📋 创建策略: {strategy_name}")
    
    # 初始化策略
    strategy.on_init()
    print(f"🔧 初始化策略完成")
    
    # 启动策略
    strategy.on_start()
    print(f"▶️  启动策略完成")
    
    try:
        print("\n🎯 策略运行中，按 Ctrl+C 退出...")
        print("💡 可以通过日志文件查看策略运行状态")
        
        while True:
            time.sleep(10)
            
            # 简单状态显示
            current_pos = float(strategy.pos)
            print(f"⏰ {time.strftime('%H:%M:%S')} | 仓位: {current_pos:.6f} | "
                  f"平均价: {strategy.avg_price:.6f} | 加仓次数: {strategy.current_increase_pos_count}")
                    
    except KeyboardInterrupt:
        print("\n🛑 接收到退出信号...")
        
        # 停止策略
        strategy.on_stop()
        print("🛑 策略已停止")
        
        # 关闭引擎
        main_engine.close()
        print("👋 程序已安全退出")

if __name__ == "__main__":
    main() 