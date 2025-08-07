#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OKX网关功能测试脚本
测试修改后的OKX网关是否支持：
1. 双向持仓模式
2. 跨币种保证金模式
3. 逐仓模式
4. 合约模式
5. 杠杆设置
"""

from howtrader.event import EventEngine
from howtrader.trader.engine import MainEngine
from howtrader.gateway.okx import OkxGateway
from howtrader.trader.object import OrderRequest, CancelRequest
from howtrader.trader.constant import Direction, OrderType, Exchange
from decimal import Decimal
import time

def test_okx_gateway():
    """测试OKX网关功能"""
    
    # 创建事件引擎和主引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 添加OKX网关
    main_engine.add_gateway(OkxGateway)
    
    # OKX连接配置 - 请替换为你的真实API参数
    okx_setting = {
        "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",
        "secret": "37C1783FB06567FE998CE1FC97FC242A", 
        "passphrase": "Qxat240925.",
        "proxy_host": "",
        "proxy_port": 0,
        "server": "TEST",  # 使用测试环境
        "position_mode": "long_short_mode",  # 双向持仓模式
        "margin_mode": "cross",  # 全仓模式，可改为"isolated"测试逐仓
        "account_type": "multi_currency"  # 跨币种保证金模式
    }
    
    try:
        print("🔗 连接OKX交易所...")
        
        # 连接OKX
        main_engine.connect(okx_setting, "OKX")
        
        # 等待连接完成
        time.sleep(3)
        
        print("✅ 连接成功！")
        
        # 获取网关实例
        gateway = main_engine.get_gateway("OKX")
        
        print("\n⚙️ 测试设置杠杆...")
        # 测试设置杠杆
        gateway.set_leverage("BTC-USDT-SWAP", 15, "cross")
        time.sleep(2)
        
        print("\n📝 测试发送订单（双向持仓模式）...")
        # 测试发送订单
        order_req = OrderRequest(
            symbol="BTC-USDT-SWAP",
            exchange=Exchange.OKX,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=Decimal("0.001"),
            price=Decimal("115000.00"),
            reference="TEST_ORDER"
        )
        
        order_id = main_engine.send_order(order_req, "OKX")
        print(f"✅ 订单发送成功，订单ID: {order_id}")
        
        # 等待订单处理
        time.sleep(2)
        
        # 取消测试订单
        if order_id:
            cancel_req = CancelRequest(
                orderid=order_id,
                symbol="BTC-USDT-SWAP",
                exchange=Exchange.OKX
            )
            main_engine.cancel_order(cancel_req, "OKX")
            print(f"✅ 订单取消成功: {order_id}")
        
        print("\n🎉 所有测试完成！")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # 断开连接
        main_engine.close()
        print("🔌 连接已断开")

if __name__ == "__main__":
    test_okx_gateway()
