#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
真实OKX交易所合约信息测试
========================

测试 getattr(self.main_engine, 'get_contract', None) 方法
向真实的OKX交易所请求PEPE-USDT-SWAP等合约数据
"""

import time
import sys
from typing import Optional, List
from decimal import Decimal

# HowTrader imports
from howtrader.event import EventEngine
from howtrader.trader.engine import MainEngine
from howtrader.trader.object import ContractData
from howtrader.trader.constant import Exchange, Product
from howtrader.gateway.okx import OkxGateway

class RealContractTester:
    """真实合约信息测试器"""
    
    def __init__(self):
        print("🚀 初始化真实合约信息测试器")
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.gateway_name = "OKX"
        self.connected = False
        
        print(f"📋 Event Engine: {type(self.event_engine)}")
        print(f"📋 Main Engine: {type(self.main_engine)}")
    
    def add_gateway_and_connect(self) -> bool:
        """添加网关并连接"""
        try:
            print(f"\n📡 添加 {self.gateway_name} 网关...")
            
            # 正确添加OKX网关
            gateway = self.main_engine.add_gateway(OkxGateway, self.gateway_name)
            print(f"✅ 网关添加成功: {type(gateway)}")
            
            # 配置连接参数 (公开数据不需要API密钥)
            setting = {
                "key": "",                    # 空字符串表示只获取公开数据
                "secret": "",
                "passphrase": "",
                "proxy_host": "",
                "proxy_port": 0,
                "server": "REAL"              # 使用真实服务器
            }
            
            print(f"📋 连接配置: {setting}")
            print(f"🔗 开始连接到 {self.gateway_name}...")
            
            # 连接到交易所
            self.main_engine.connect(setting, self.gateway_name)
            
            # 等待连接和数据加载
            print("⏳ 等待合约数据加载...")
            for i in range(20):  # 等待最多20秒
                time.sleep(1)
                
                # 检查get_contract方法是否可用
                get_contract_func = getattr(self.main_engine, 'get_contract', None)
                if get_contract_func:
                    print(f"✅ get_contract 方法已可用: {type(get_contract_func)}")
                    
                    # 检查是否有合约数据
                    get_all_contracts = getattr(self.main_engine, 'get_all_contracts', None)
                    if get_all_contracts:
                        contracts = get_all_contracts()
                        if contracts and len(contracts) > 0:
                            self.connected = True
                            print(f"🎉 连接成功！获取到 {len(contracts)} 个合约")
                            return True
                
                print(f"   等待中... ({i+1}/20)")
            
            print("❌ 连接超时 - 未能获取到合约数据")
            return False
            
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def test_get_contract_method(self) -> None:
        """测试 get_contract 方法"""
        print(f"\n🔬 测试 main_engine.get_contract 方法")
        print("=" * 50)
        
        # 1. 测试 getattr 方法
        get_contract_func = getattr(self.main_engine, 'get_contract', None)
        print(f"1️⃣ getattr 结果: {get_contract_func}")
        print(f"   类型: {type(get_contract_func)}")
        print(f"   是否可调用: {callable(get_contract_func)}")
        
        if not get_contract_func:
            print("❌ get_contract 方法不存在")
            return
        
        # 2. 测试具体合约查询
        test_symbols = [
            "PEPE-USDT-SWAP.OKX",
            "BTC-USDT-SWAP.OKX", 
            "ETH-USDT-SWAP.OKX",
            "DOGE-USDT-SWAP.OKX"
        ]
        
        print(f"\n2️⃣ 测试合约查询:")
        for symbol in test_symbols:
            print(f"\n🔍 查询: {symbol}")
            try:
                contract = get_contract_func(symbol)
                if contract:
                    self.print_contract_info(contract)
                else:
                    print(f"❌ 未找到合约: {symbol}")
                    
                    # 尝试查找相似合约
                    self.find_similar_contracts(symbol.replace(".OKX", ""))
                    
            except Exception as e:
                print(f"❌ 查询失败: {e}")
    
    def print_contract_info(self, contract: ContractData) -> None:
        """打印合约详细信息"""
        print(f"✅ 合约信息:")
        print(f"   🏷️  symbol: {contract.symbol}")
        print(f"   🏛️  exchange: {contract.exchange}")
        print(f"   📝  name: {contract.name}")
        print(f"   🎯  product: {contract.product}")
        print(f"   📊  vt_symbol: {contract.vt_symbol}")
        
        print(f"   💰 交易规则:")
        print(f"      📏 price_tick: {contract.pricetick}")
        print(f"      ⚖️  size: {contract.size}")
        print(f"      📦 min_volume: {contract.min_volume}")
        
        # 检查OKX特有字段
        if hasattr(contract, 'min_size'):
            min_size = getattr(contract, 'min_size', 'N/A')
            print(f"      🎯 min_size: {min_size}")
        
        if hasattr(contract, 'min_notional'):
            print(f"      💵 min_notional: {contract.min_notional}")
        
        print(f"   🔧 功能:")
        print(f"      🛑 stop_supported: {contract.stop_supported}")
        print(f"      🔄 net_position: {contract.net_position}")
        print(f"      📈 history_data: {contract.history_data}")
    
    def find_similar_contracts(self, search_term: str) -> None:
        """查找相似合约"""
        get_all_contracts = getattr(self.main_engine, 'get_all_contracts', None)
        if not get_all_contracts:
            print("   ❌ get_all_contracts 方法不可用")
            return
        
        all_contracts = get_all_contracts()
        search_upper = search_term.upper()
        
        similar = []
        for contract in all_contracts:
            if search_upper in contract.symbol.upper():
                similar.append(contract)
        
        if similar:
            print(f"   🔎 找到 {len(similar)} 个相似合约:")
            for i, contract in enumerate(similar[:5]):  # 只显示前5个
                print(f"      {i+1}. {contract.vt_symbol}")
        else:
            print(f"   ❌ 未找到包含 '{search_term}' 的合约")
    
    def test_martin_contract_usage(self) -> None:
        """测试马丁策略中的合约使用方式"""
        print(f"\n🧮 测试马丁策略合约使用方式")
        print("=" * 50)
        
        # 模拟SimpleMartinManager中的合约加载过程
        symbol = "PEPE-USDT-SWAP.OKX"
        
        print(f"📋 模拟马丁管理器加载合约: {symbol}")
        
        try:
            # 1. 获取get_contract方法 (这是我们在SimpleMartinManager中使用的方式)
            get_contract = getattr(self.main_engine, 'get_contract', None)
            print(f"1️⃣ getattr 获取方法: {'成功' if get_contract else '失败'}")
            
            if get_contract:
                # 2. 获取合约信息
                contract = get_contract(symbol)
                print(f"2️⃣ 获取合约信息: {'成功' if contract else '失败'}")
                
                if contract:
                    # 3. 提取马丁策略需要的关键信息
                    price_tick = contract.pricetick
                    contract_size = contract.size
                    
                    # 根据交易所获取最小下单单位
                    if contract.exchange == Exchange.OKX:
                        min_size = getattr(contract, 'min_size', None)
                        if min_size and min_size > 0:
                            min_order_size = min_size
                        else:
                            min_order_size = contract.min_volume
                    else:
                        min_order_size = contract.min_volume
                    
                    print(f"3️⃣ 提取关键信息:")
                    print(f"   📏 价格精度: {price_tick}")
                    print(f"   ⚖️  合约乘数: {contract_size}")
                    print(f"   🎯 最小下单单位: {min_order_size}")
                    
                    # 4. 测试订单数量调整
                    test_volume = 123.456789
                    min_size_float = float(min_order_size)
                    adjusted_volume = max(min_size_float, round(test_volume / min_size_float) * min_size_float)
                    
                    print(f"4️⃣ 订单调整测试:")
                    print(f"   原始数量: {test_volume}")
                    print(f"   调整后数量: {adjusted_volume}")
                    
                    # 5. 测试价格调整
                    test_price = 0.00001234567
                    price_tick_float = float(price_tick)
                    adjusted_price = round(test_price / price_tick_float) * price_tick_float
                    
                    print(f"5️⃣ 价格调整测试:")
                    print(f"   原始价格: {test_price}")
                    print(f"   调整后价格: {adjusted_price}")
                    
                else:
                    print("❌ 无法获取合约信息")
            else:
                print("❌ 无法获取 get_contract 方法")
                
        except Exception as e:
            print(f"❌ 测试过程出错: {e}")
            import traceback
            traceback.print_exc()
    
    def list_all_available_contracts(self) -> None:
        """列出所有可用合约"""
        print(f"\n📋 列出所有可用合约")
        print("=" * 50)
        
        get_all_contracts = getattr(self.main_engine, 'get_all_contracts', None)
        if not get_all_contracts:
            print("❌ get_all_contracts 方法不可用")
            return
        
        all_contracts = get_all_contracts()
        print(f"总共获取到 {len(all_contracts)} 个合约")
        
        # 按产品类型分组
        futures_contracts = []
        spot_contracts = []
        other_contracts = []
        
        for contract in all_contracts:
            if contract.product == Product.FUTURES:
                futures_contracts.append(contract)
            elif contract.product == Product.SPOT:
                spot_contracts.append(contract)
            else:
                other_contracts.append(contract)
        
        print(f"\n📊 合约分类:")
        print(f"   🔮 期货合约: {len(futures_contracts)} 个")
        print(f"   💰 现货合约: {len(spot_contracts)} 个")
        print(f"   🎯 其他合约: {len(other_contracts)} 个")
        
        # 显示PEPE相关合约
        pepe_contracts = [c for c in all_contracts if "PEPE" in c.symbol.upper()]
        print(f"\n🐸 PEPE相关合约 ({len(pepe_contracts)} 个):")
        for contract in pepe_contracts:
            print(f"   {contract.vt_symbol} ({contract.product.value})")
        
        # 显示部分热门合约
        popular_symbols = ["BTC", "ETH", "DOGE", "SHIB", "PEPE"]
        print(f"\n🔥 热门合约样本:")
        for symbol_part in popular_symbols:
            matches = [c for c in all_contracts if symbol_part in c.symbol.upper() and "SWAP" in c.symbol]
            if matches:
                contract = matches[0]  # 取第一个
                print(f"   {contract.vt_symbol}: size={contract.size}, tick={contract.pricetick}")
    
    def disconnect(self) -> None:
        """断开连接"""
        try:
            print("\n🔌 断开连接...")
            self.main_engine.close()
            self.event_engine.stop()
            print("✅ 断开完成")
        except Exception as e:
            print(f"❌ 断开失败: {e}")

def main():
    """主测试函数"""
    print("🧪 真实OKX交易所合约信息测试")
    print("🎯 测试 getattr(self.main_engine, 'get_contract', None) 方法")
    print("=" * 60)
    
    tester = RealContractTester()
    
    try:
        # 1. 连接到交易所
        if not tester.add_gateway_and_connect():
            print("❌ 无法连接到交易所，退出测试")
            return
        
        # 2. 测试 get_contract 方法
        tester.test_get_contract_method()
        
        # 3. 测试马丁策略使用方式
        tester.test_martin_contract_usage()
        
        # 4. 列出所有合约
        tester.list_all_available_contracts()
        
        print(f"\n✅ 所有测试完成！")
        
    except KeyboardInterrupt:
        print(f"\n⏹️ 测试被用户中断")
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main() 