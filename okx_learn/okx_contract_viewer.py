"""
OKX合约查看器
- 查看所有现货品种
- 查看所有永续合约
- 查看所有交割合约
- 显示symbol命名规则
"""

import time
from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine
from howtrader.gateway.okx import OkxGateway
from howtrader.trader.object import ContractData

# 配置日志
SETTINGS["log.active"] = True
SETTINGS["log.console"] = True

# OKX API 配置
OKX_SETTING = {
    "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",
    "secret": "37C1783FB06567FE998CE1FC97FC242A", 
    "passphrase": "Qxat240925.",
    "proxy_host": "",
    "proxy_port": 0,
    "server": "REAL"
}

def show_okx_contracts():
    """显示OKX支持的所有合约"""
    print("🔍 查询OKX支持的合约...")
    
    # 初始化连接
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(OkxGateway)
    
    print("🔗 连接OKX...")
    main_engine.connect(OKX_SETTING, "OKX")
    time.sleep(15)  # 等待合约查询完成
    
    gateway = main_engine.get_gateway("OKX")
    if not gateway:
        print("❌ 连接失败")
        return
    
    # 获取所有合约 
    contracts = main_engine.get_all_contracts()
    
    # 按产品类型分组
    spot_contracts = []      # 现货
    swap_contracts = []      # 永续合约  
    futures_contracts = []   # 交割合约
    
    for contract in contracts:
        if contract.product.value == "现货":
            spot_contracts.append(contract)
        elif contract.product.value == "期货":
            if "SWAP" in contract.symbol:
                swap_contracts.append(contract)
            else:
                futures_contracts.append(contract)
    
    print(f"\n📊 OKX合约统计:")
    print(f"现货品种: {len(spot_contracts)}")
    print(f"永续合约: {len(swap_contracts)}")
    print(f"交割合约: {len(futures_contracts)}")
    print(f"总计: {len(contracts)}")
    
    # 显示热门现货
    print(f"\n💰 现货品种 (前20个):")
    print("=" * 60)
    btc_spots = [c for c in spot_contracts if "BTC" in c.symbol]
    eth_spots = [c for c in spot_contracts if "ETH" in c.symbol]
    other_spots = [c for c in spot_contracts if "BTC" not in c.symbol and "ETH" not in c.symbol]
    
    popular_spots = btc_spots[:5] + eth_spots[:5] + other_spots[:10]
    
    for i, contract in enumerate(popular_spots, 1):
        print(f"{i:2d}. {contract.symbol:<15} | 最小下单: {contract.min_volume}")
    
    # 显示热门永续合约
    print(f"\n🔄 永续合约 (前20个):")
    print("=" * 60)
    btc_swaps = [c for c in swap_contracts if "BTC" in c.symbol]
    eth_swaps = [c for c in swap_contracts if "ETH" in c.symbol]
    other_swaps = [c for c in swap_contracts if "BTC" not in c.symbol and "ETH" not in c.symbol]
    
    popular_swaps = btc_swaps[:5] + eth_swaps[:5] + other_swaps[:10]
    
    for i, contract in enumerate(popular_swaps, 1):
        print(f"{i:2d}. {contract.symbol:<20} | 合约乘数: {contract.size}")
    
    # 显示交割合约示例
    if futures_contracts:
        print(f"\n📅 交割合约 (前10个):")
        print("=" * 60)
        for i, contract in enumerate(futures_contracts[:10], 1):
            print(f"{i:2d}. {contract.symbol:<25} | 合约乘数: {contract.size}")
    
    # 显示命名规则总结
    print(f"\n📝 OKX Symbol命名规则:")
    print("=" * 50)
    print("现货:     BTC-USDT, ETH-USDT, SOL-USDT")
    print("永续合约: BTC-USDT-SWAP, ETH-USDT-SWAP")  
    print("交割合约: BTC-USDT-241227, ETH-USDT-250328")
    print("期权:     BTC-USD-241227-100000-C")
    
    main_engine.close()

if __name__ == "__main__":
    show_okx_contracts() 