#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CTA马丁格尔策略启动脚本
基于HowTrader标准启动方式
"""

from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine
from howtrader.gateway.okx import OkxGateway
from howtrader.app.cta_strategy import CtaStrategyApp
from howtrader_martingale_strategy import HowTraderMartingaleStrategy

SETTINGS["log.active"] = True
SETTINGS["log.level"] = 20
SETTINGS["log.console"] = True

# OKX连接配置
okx_setting = {
    "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",
    "secret": "37C1783FB06567FE998CE1FC97FC242A", 
    "passphrase": "Qxat240925.",
    "proxy_host": "",
    "proxy_port": 0,
    "server": "REAL"
}

# 马丁策略配置
martingale_setting = {
    "lever": 10,                    # 杠杆倍数
    "first_margin": 20.0,           # 首次开仓保证金
    "first_margin_add": 20.0,       # 加仓保证金
    "adding_number": 15,            # 最大加仓次数
    "amount_multiplie": 1.15,       # 加仓倍数
    "price_multiple": 1.0,          # 价格间隔倍数
    "profit_target": 0.008,         # 止盈比例
    "opp_ratio": 0.018,             # 加仓触发比例
    "mode": 1,                      # 1=做多, 2=做空
    "max_position_value": 5000.0,   # 最大仓位价值
    "min_notional": 11.0,           # 最小交易金额
    "enable_option_hedge": False,   # 期权对冲
}

def main():
    """
    主函数
    """
    # 创建主引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 添加网关
    main_engine.add_gateway(OkxGateway)
    
    # 创建应用
    cta_engine = main_engine.add_app(CtaStrategyApp)
    main_engine.write_log("主引擎创建成功")
    
    # 连接网关
    main_engine.connect(okx_setting, "OKX")
    main_engine.write_log("连接OKX接口")
    
    # 等待一段时间让连接建立
    import time
    time.sleep(10)
    
    # 创建策略
    cta_engine.add_strategy(
        class_name="HowTraderMartingaleStrategy",
        strategy_name="MartingaleBTC", 
        vt_symbol="BTC-USDT-SWAP.OKX",
        setting=martingale_setting
    )
    
    # 初始化策略
    cta_engine.init_strategy("MartingaleBTC")
    
    # 启动策略
    cta_engine.start_strategy("MartingaleBTC")
    
    main_engine.write_log("马丁策略启动完成")
    
    input("按回车键退出\n")
    
    # 停止策略
    cta_engine.stop_strategy("MartingaleBTC")
    
    # 关闭主引擎
    main_engine.close()

if __name__ == "__main__":
    main() 