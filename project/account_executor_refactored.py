#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
单账户专用马丁策略执行器 - 买单队列版本
===============================================================

设计原则：
- 进程隔离：每个账户独立运行，崩溃不互相影响
- 配置隔离：独立的API配置和交易对设置
- 网关隔离：独立的网关连接和认证
- 日志隔离：独立的日志输出，便于问题排查
- 状态隔离：独立的内存状态，无持久化依赖

马丁策略逻辑：
- 首次开仓：市价开仓 → 挂10个买单 + 1个卖单
- 买单成交：成交N笔 → 取消旧卖单 → 补充N笔买单 → 重新挂卖单
- 卖单成交：完全成交 → 取消所有买单 → 重置策略 → 开始新周期
- 买单队列：始终保持10个活跃买单在市场上

架构特点：
- 去除指令抽象层，直接操作
- 买单队列管理：自动补充成交的买单
- 三层职责分离：订单管理 | 策略管理 | 执行器

版本：v3.0 (买单队列直接操作版)
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum

from howtrader.event import Event, EventEngine
from howtrader.trader.engine import MainEngine
from howtrader.trader.object import (
    OrderData, TradeData, PositionData, ContractData,
    OrderRequest, CancelRequest, SubscribeRequest
)
from howtrader.trader.event import EVENT_ORDER, EVENT_TRADE, EVENT_POSITION
from howtrader.trader.constant import Direction, Offset, Status, OrderType, Exchange
from howtrader.trader.utility import extract_vt_symbol


# =============================================================================
# 数据结构定义
# =============================================================================

@dataclass
class BuyOrderInfo:
    """买单信息"""
    order_id: str
    price: float
    volume: float
    level: int                    # 价格层级 (1-10)
    add_sequence: int             # 🎯 NEW: 加仓序号
    created_time: datetime
    is_filled: bool = False


@dataclass
class MartinState:
    """马丁策略状态"""
    symbol: str
    mode: int                           # 1=做多, 2=做空
    avg_price: float                    # 平均成本价
    position_size: float                # 仓位大小
    add_count: int                      # 已加仓次数
    max_add_count: int                  # 最大加仓次数
    total_margin_used: float            # 已使用保证金
    execution_mode: str                 # 执行模式 ("normal", "suspended", etc.)
    last_update: datetime
    is_active: bool = True              # 是否活跃


@dataclass
class AccountConfig:
    """单账户配置"""
    account_id: str                     # 账户ID
    api_key: str                        # API密钥
    api_secret: str                     # API密钥
    api_passphrase: str                 # API密码(OKX需要)
    gateway_name: str                   # 网关名称 (如: "OKX")
    test_mode: bool = True              # 是否测试模式
    supported_symbols: Optional[List[str]] = None # 支持的交易对列表


# =============================================================================
# 马丁订单管理器 - 支持买单队列管理
# =============================================================================

class MartinOrderManager:
    """马丁订单管理器 - 专注买单队列和订单分类"""
    
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.order_sequence = 0
        
        # 基础订单管理
        self.order_references: Dict[str, str] = {}          # order_id -> reference
        
        # 🎯 按策略分类的活跃订单
        self.strategy_buy_orders: Dict[str, Dict[str, BuyOrderInfo]] = {}  # strategy_key -> {order_id: BuyOrderInfo}
        self.strategy_sell_orders: Dict[str, Set[str]] = {}                # strategy_key -> {order_ids}
        self.order_strategy_mapping: Dict[str, str] = {}                   # order_id -> strategy_key

        # 🎯 NEW: 加仓序号管理
        self.strategy_add_sequence: Dict[str, int] = {}  # strategy_key -> 下一个加仓序号
        
        # 买单队列配置
        self.target_buy_orders_count = 10                   # 目标买单数量
    
    # 🎯 NEW: 分配加仓序号
    def allocate_add_sequence(self, strategy_key: str) -> int:
        """为策略分配下一个加仓序号"""
        if strategy_key not in self.strategy_add_sequence:
            self.strategy_add_sequence[strategy_key] = 1
        
        sequence = self.strategy_add_sequence[strategy_key]
        self.strategy_add_sequence[strategy_key] += 1
        return sequence

    def generate_order_reference(self, symbol: str, action: str, direction: str = "LONG") -> str:
        """
        生成订单reference - 包含多空方向信息
        
        Args:
            symbol: 交易对
            action: 操作类型 (OPEN, BUY_L1, PROFIT, REFILL_L1等)
            direction: 多空方向 ("LONG" 或 "SHORT")
        """
        self.order_sequence += 1
        clean_symbol = symbol.replace('-', '').replace('.', '').replace('_', '')
        return f"MARTIN_{direction}_{clean_symbol}_{action}_{self.order_sequence:04d}"
    
    def register_strategy_buy_order(self, strategy_key: str, order_id: str, 
                                  reference: str, price: float, avg_price: float, volume: float, total_volume: float, add_sequence: int) -> None:
        """注册策略买单"""
        self.order_references[order_id] = reference
        self.order_strategy_mapping[order_id] = strategy_key
        
        # 初始化策略订单容器
        if strategy_key not in self.strategy_buy_orders:
            self.strategy_buy_orders[strategy_key] = {}
        
        # 添加买单信息
        buy_info = BuyOrderInfo(
            order_id=order_id,
            price=price,
            avg_price=avg_price,
            volume=volume,
            total_volume=total_volume,
            add_sequence=add_sequence,  # 🎯 第几次买单
            created_time=datetime.now()
        )
        self.strategy_buy_orders[strategy_key][order_id] = buy_info
        
        print(f"[{self.account_id}] 注册买单: {strategy_key} 第几张挂单{add_sequence} 价格={price:.10f}")
    
    def register_strategy_sell_order(self, strategy_key: str, order_id: str, reference: str) -> None:
        """注册策略卖单"""
        self.order_references[order_id] = reference
        self.order_strategy_mapping[order_id] = strategy_key
        
        if strategy_key not in self.strategy_sell_orders:
            self.strategy_sell_orders[strategy_key] = set()
        
        self.strategy_sell_orders[strategy_key].add(order_id)
        print(f"[{self.account_id}] 注册卖单: {strategy_key}")
    
    def mark_buy_order_filled(self, order_id: str, strategy_key: str) -> Optional[tuple]:
        """标记买单成交，返回(strategy_key, buy_info)"""
        
        if strategy_key and order_id in self.strategy_buy_orders.get(strategy_key, {}):
            # 从买单映射列表中移除该买单信息，并返回对应的buy_info
            buy_info = self.strategy_buy_orders[strategy_key].pop(order_id)
            buy_info.is_filled = True
            self.order_references.pop(order_id, None)
            self.order_strategy_mapping.pop(order_id, None)

            # �� 获取加仓信息
            max_add_sequence = 0
            for buy_info in self.strategy_buy_orders[strategy_key].values():
                max_add_sequence = max(max_add_sequence, buy_info.add_sequence)
                 
            print(f"[{self.account_id}] 买单成交: {strategy_key} Level{buy_info.level}")
            return buy_info,max_add_sequence
        return None
    
    def mark_sell_order_filled(self, order_id: str) -> Optional[str]:
        """标记卖单成交，返回strategy_key"""
        strategy_key = self.order_strategy_mapping.get(order_id)
        if strategy_key and order_id in self.strategy_sell_orders.get(strategy_key, set()):
            self.strategy_sell_orders[strategy_key].discard(order_id)
            self.order_references.pop(order_id, None)
            self.order_strategy_mapping.pop(order_id, None)
            
            print(f"[{self.account_id}] 卖单成交: {strategy_key}")
            return strategy_key
        return None
    
    def get_strategy_buy_orders_count(self, strategy_key: str) -> int:
        """获取策略的活跃买单数量"""
        return len(self.strategy_buy_orders.get(strategy_key, {}))
    
    def get_strategy_missing_buy_orders(self, strategy_key: str) -> int:
        """获取策略缺失的买单数量"""
        current_count = self.get_strategy_buy_orders_count(strategy_key)
        return max(0, self.target_buy_orders_count - current_count)
    
    def get_strategy_sell_orders(self, strategy_key: str) -> List[str]:
        """获取策略的活跃卖单ID列表"""
        return list(self.strategy_sell_orders.get(strategy_key, set()))
    
    def clear_strategy_orders(self, strategy_key: str) -> tuple:
        """清除策略的所有订单，返回(buy_order_ids, sell_order_ids)"""
        buy_order_ids = list(self.strategy_buy_orders.get(strategy_key, {}).keys())
        sell_order_ids = list(self.strategy_sell_orders.get(strategy_key, set()))
        
        # 清除记录
        self.strategy_buy_orders.pop(strategy_key, None)
        self.strategy_sell_orders.pop(strategy_key, None)
        
        for order_id in buy_order_ids + sell_order_ids:
            self.order_references.pop(order_id, None)
            self.order_strategy_mapping.pop(order_id, None)
        
        return buy_order_ids, sell_order_ids
    
    def get_order_strategy(self, order_id: str) -> Optional[str]:
        """获取订单所属策略"""
        return self.order_strategy_mapping.get(order_id)
    
    def classify_order(self, order: OrderData) -> str:
        """根据订单reference分类订单"""
        if hasattr(order, 'reference') and order.reference:
            if order.reference.startswith('MARTIN_'):
                return "martin"
            elif order.reference.startswith('MANUAL_'):
                return "manual"
        return "unknown"
   

# =============================================================================
# 简化马丁策略管理器 - 纯状态管理
# =============================================================================

class SimpleMartinManager:
    """简化马丁策略管理器 - 专注状态管理和价格计算"""
    
    def __init__(self, account_id: str, symbol: str, mode: int, config: dict, contract_info: ContractData):
        self.account_id = account_id
        self.symbol = symbol
        self.mode = mode
        self.config = config
        self.contract = contract_info
        
        # 马丁参数
        self.lever = config.get('lever', 10)                       # 杠杆倍数
        self.first_margin = config.get('first_margin', 50.0)       # 首次保证金
        self.first_margin_add = config.get('first_margin_add', 50.0)  # 加仓保证金
        self.adding_number = config.get('adding_number', 20)       # 最大加仓次数
        self.amount_multiplier = config.get('amount_multiplier', 1.2)  # 加仓金额倍数
        self.price_multiple = config.get('price_multiple', 1.1)     # 价格倍数
        self.profit_target = config.get('profit_target', 0.01)     # 止盈比例
        self.opp_ratio = config.get('opp_ratio', 0.025)           # 默认止盈触发比例
        self.buy_orders_count= config.get('buy_orders_count', 10) # 买单队列数量

        
        # 合约信息
        self.price_tick = contract_info.pricetick  # 价格精度
        self.contract_size = contract_info.size   # 合约乘数
        self.min_order_size = self._get_min_order_size(contract_info)   # 最小下单单位
        
        # 策略状态
        self.state = MartinState(
            symbol=symbol,
            mode=mode,
            avg_price=0.0,
            position_size=0.0,
            add_count=0,
            total_margin_used=0.0,
            execution_mode="normal",
            last_update=datetime.now()
        )
        
        mode_text = "做多" if mode == 1 else "做空"
        print(f"[{account_id}] 创建队列马丁策略: {symbol} {mode_text}")
        print(f"  买单队列数量: {self.buy_orders_count}")
        print(f"  价格步长: {self.price_step_ratio:.1%}")
        print(f"  每层保证金: {self.margin_per_level}")
        
    def update_position_on_buy(self, trade_price: float, trade_volume: float) -> None:
        """买入时更新仓位"""
        if self.state.position_size > 0:
            total_cost = self.state.avg_price * self.state.position_size + trade_price * trade_volume
            self.state.position_size += trade_volume
            self.state.avg_price = total_cost / self.state.position_size
        else:
            self.state.avg_price = trade_price
            self.state.position_size = trade_volume
        
        self.state.add_count += 1
        self.state.last_update = datetime.now()
        
        print(f"[{self.account_id}] 买入更新: 仓位={self.state.position_size:.6f} 成本={self.state.avg_price:.6f} 第{self.state.add_count}次")
        
    def update_position_on_sell(self, trade_volume: float) -> None:
        """卖出时更新仓位"""
        self.state.position_size -= trade_volume
        self.state.last_update = datetime.now()
        
        print(f"[{self.account_id}] 卖出更新: 剩余仓位={self.state.position_size:.6f}")
        
        if self.state.position_size <= float(self.min_order_size):
            self.reset_state()
    
    def calculate_first_order_params(self, current_price: float) -> Optional[tuple]:
        """计算首次开仓参数，返回(price, volume)"""
        
        older_pos=self.first_margin*self.lever/(current_price*self.contract_size)
        volume = self._round_to_size_tick(older_pos)
        self.state.avg_price = current_price
        self.state.position_size = volume      
    
        return  volume
        
    
    def calculate_buy_orders_queue(self, base_price: float,add_count:int, max_add_sequence:int) -> List[tuple]:
        """计算买单队列价格和数量，返回[(price, volume, add_sequence), ...]"""
        orders = []
        # 计算还可以挂多少单（最大加仓次数 - 已加仓次数）
        # 如果是首次开仓（add_count==0），则挂 buy_orders_count 个买单
        # 否则每次只挂1单（即加仓时只补1单）
        # 返回 [(price, volume, add_sequence,), ...]
        before_price=base_price
        if add_count == 0:
            if self.mode == 1:
                for i in range(0, self.buy_orders_count ):
                    older_price=before_price*(1-self.opp_ratio*self.price_multiple**i)
                    #older_price=before_price*(1-opp_ratio*price_multiple**i)
                    older_margin=self.first_margin_add*self.amount_multiplier**i
                    #older_margin=first_margin_add*amount_multiplie**i
                    older_pos=older_margin*self.lever/(older_price*self.contract_size)
                    volume = self._round_to_size_tick(older_pos)
                    #older_pos=older_margin*lever/(older_price*ctVal)
                    
                    #average_cost = (average_cost*total_pos +older_margin*lever)/(total_pos+older_pos)
                    self.state.avg_price = (self.state.avg_price*self.state.position_size +older_margin*self.lever)/(self.state.position_size+volume)
                    self.state.position_size = self.state.position_size+volume
                    orders.append((older_price, volume, i+1,self.state.avg_price,self.state.position_size)) 
                    before_price=older_price
                    #total_pos = total_pos+older_pos
            elif self.mode == 2:
                for i in range(0, self.buy_orders_count ):
                    older_price=before_price*(1+self.opp_ratio*self.price_multiple**i)
                    older_margin=self.first_margin_add*self.amount_multiplier**i
                    older_pos=older_margin*self.lever/(older_price*self.contract_size)
                    volume = self._round_to_size_tick(older_pos)
                    self.state.avg_price = (self.state.avg_price*self.state.position_size +older_margin*self.lever)/(self.state.position_size+volume)
                    self.state.position_size = self.state.position_size+volume
                    orders.append((older_price, volume, i+1,self.state.avg_price,self.state.position_size))     
                    before_price=older_price
        elif max_add_sequence < self.buy_orders_count:
             #计算增加挂单的要素
             if self.mode == 1:
                older_price=before_price*(1-self.opp_ratio*self.price_multiple**max_add_sequence)
                older_margin=self.first_margin_add*self.amount_multiplier**max_add_sequence
                older_pos=older_margin*self.lever/(older_price*self.contract_size)
                volume = self._round_to_size_tick(older_pos)
                self.state.avg_price = (self.state.avg_price*self.state.position_size +older_margin*self.lever)/(self.state.position_size+volume)
                self.state.position_size = self.state.position_size+volume
                orders.append((older_price, volume, i+1,self.state.avg_price,self.state.position_size))     
                before_price=older_price
             elif self.mode == 2:
                older_price=before_price*(1+self.opp_ratio*self.price_multiple**max_add_sequence)
                older_margin=self.first_margin_add*self.amount_multiplier**max_add_sequence
                older_pos=older_margin*self.lever/(older_price*self.contract_size)
                volume = self._round_to_size_tick(older_pos)
                self.state.avg_price = (self.state.avg_price*self.state.position_size +older_margin*self.lever)/(self.state.position_size+volume)
                self.state.position_size = self.state.position_size+volume
                orders.append((older_price, volume, i+1,self.state.avg_price,self.state.position_size))     
                before_price=older_price
        else:
             return None # 不需要下单
    
        return orders
    
    def calculate_sell_order_params(self,avg_price:float,total_volume:float, add_sequence:int) -> Optional[tuple]:
        """计算卖单价格和数量，返回(price, volume)"""
        
        # 动态止盈比例
        profit_target = self._calculate_profit_target(add_sequence)
        
        if self.mode == 1:  # 做多
            price = avg_price * (1 + profit_target)
        else:  # 做空
            price = avg_price * (1 - profit_target)
        
        volume = self._round_to_size_tick(total_volume)
        
        return price, volume
    
    def get_health_status(self) -> dict:
        """获取策略健康状态"""
        return {
            'symbol': self.symbol,
            'mode': '做多' if self.mode == 1 else '做空',
            'execution_mode': self.state.execution_mode,
            'position_size': self.state.position_size,
            'avg_price': self.state.avg_price,
            'add_count': self.state.add_count,
            'is_healthy': self._check_health(),
            'last_update': self.state.last_update.isoformat()
        }
    
    def reset_state(self) -> None:
        """重置策略状态"""
        print(f"[{self.account_id}] {self.symbol} 重置马丁状态: 完成{self.state.add_count}次加仓")
        self.state.avg_price = 0.0
        self.state.position_size = 0.0
        self.state.add_count = 0
        self.state.total_margin_used = 0.0
        self.state.execution_mode = "normal"
        self.state.last_update = datetime.now()
    
    def _calculate_profit_target(self) -> float:
        """根据加仓次数计算止盈比例"""
        if self.state.add_count <= 3:
            return 0.01      # 1%
        elif self.state.add_count <= 6:
            return 0.012     # 1.2%
        elif self.state.add_count <= 10:
            return 0.015     # 1.5%
        elif self.state.add_count <= 15:
            return 0.02      # 2%
        else:
            return 0.025     # 2.5%
    
    def _check_health(self) -> bool:
        """检查策略健康状态"""
        # 检查更新时间
        if (datetime.now() - self.state.last_update).total_seconds() > 600:  # 10分钟无更新
            return False
        
        # 检查加仓次数
        if self.state.add_count >= self.max_add_count:
            return False
        
        return True
    
    def _get_min_order_size(self, contract: ContractData) -> Decimal:
        """获取最小下单单位"""
        from howtrader.trader.constant import Exchange
        try:
            if contract.exchange == Exchange.OKX:
                return contract.min_size
            else:
                return contract.min_volume
        except Exception:
            return Decimal("0.001")
    
    def _round_to_size_tick(self, volume: float) -> float:
        """调整到合约最小单位"""
        return max(float(self.min_order_size), round(volume / float(self.min_order_size)) * float(self.min_order_size))
    
    def get_state(self) -> MartinState:
        """获取策略状态"""
        return self.state


# =============================================================================
# 单账户专用执行器 - 重构版本
# =============================================================================

class SingleAccountExecutor:
    """单账户执行器 - 简化架构，直接操作"""
    
    def __init__(self, config: AccountConfig, main_engine: MainEngine, event_engine: EventEngine):
        self.config = config
        self.account_id = config.account_id
        self.main_engine = main_engine
        self.event_engine = event_engine
        
        # 组件初始化
        self.order_manager = MartinOrderManager(self.account_id)
        self.martin_managers: Dict[str, SimpleMartinManager] = {}
        
        # 执行器状态
        self.active = False
        self.supported_symbols: Set[str] = set(config.supported_symbols or [])
        
        # 定时器和统计
        self.timer_thread: Optional[threading.Thread] = None
        self.timer_interval = 10
        self.stats = {
            'total_orders': 0,
            'total_trades': 0,
            'start_time': datetime.now(),
            'last_activity': datetime.now()
        }
        
        print(f"[{self.account_id}] 初始化单账户执行器: 支持{len(self.supported_symbols)}个交易对")
    
    def start(self) -> None:
        """启动执行器"""
        if self.active:
            print(f"[{self.account_id}] 执行器已经在运行中")
            return
        
        print(f"[{self.account_id}] 启动单账户执行器...")
        
        # 注册事件监听
        self._register_events()
        
        # 启动定时器
        self._start_timer()
        
        self.active = True
        print(f"[{self.account_id}] 单账户执行器启动成功！")
    
    def stop(self) -> None:
        """停止执行器"""
        if not self.active:
            return
        
        print(f"[{self.account_id}] 停止单账户执行器...")
        
        # 停止定时器
        self._stop_timer()
        
        # 注销事件监听
        self._unregister_events()
        
        self.active = False
        print(f"[{self.account_id}] 单账户执行器已停止")
    
    def add_martin_strategy(self, symbol: str, mode: int, config: dict) -> bool:
        """添加马丁策略 - 直接启动"""
        if symbol not in self.supported_symbols:
            print(f"[{self.account_id}] 错误: 交易对 {symbol} 不在支持列表中")
            return False

        strategy_key = f"{symbol}_M{mode}"
        if strategy_key in self.martin_managers:
            print(f"[{self.account_id}] 策略已存在: {strategy_key}")
            return False
        
        try:
            # 获取合约信息和当前价格
            contract_info = self._get_contract_info(symbol)
            
            # 设置合约杠杆倍数
            if contract_info:
                # 获取网关实例并设置杠杆
                gateway = self.main_engine.get_gateway(self.config.gateway_name)
                if gateway and hasattr(gateway, 'set_leverage'):
                    lever = config.get('lever', 10)
                    margin_mode = config.get('margin_mode', 'cross')  # cross 或 isolated
                    self._set_contract_leverage(symbol, lever, margin_mode)
            
            current_price = self._get_current_price(symbol)

            if not contract_info or not current_price:
                print(f"[{self.account_id}] 错误: 无法获取合约信息或价格 {symbol}")
                return False
            
            # 创建马丁管理器
            martin_manager = SimpleMartinManager(
                account_id=self.account_id,
                symbol=symbol,
                mode=mode,
                config=config,
                contract_info=contract_info
            )
            self.martin_managers[strategy_key] = martin_manager
            
            # 🎯 直接执行首次开仓
            self._execute_first_open_order(strategy_key)
            
            print(f"[{self.account_id}] ✅ 马丁策略启动成功: {strategy_key}")
            return True
            
        except Exception as e:
            print(f"[{self.account_id}] ❌ 添加策略失败: {e}")
            return False

    def on_order(self, event: Event) -> None:
        """订单事件处理 - 直接操作"""
        order: OrderData = event.data
        
        if order.status != Status.ALLTRADED:
            return
        
        try:
            strategy_key = self.order_manager.get_order_strategy(order.vt_orderid)
            if not strategy_key or strategy_key not in self.martin_managers:
                return
            
            martin_manager = self.martin_managers[strategy_key]
            #current_price = self._get_current_price(order.vt_symbol)
            
            #if not current_price:   
            #    print(f"[{self.account_id}] 错误: 无法获取当前价格 {order.vt_symbol}")
            #    return
            
            # 🎯 直接处理买单成交
            if order.direction == Direction.LONG:
                self._handle_buy_order_filled(strategy_key, order)
            
            # 🎯 直接处理卖单成交
            elif order.direction == Direction.SHORT:
                self._handle_sell_order_filled(strategy_key, order)
                
        except Exception as e:
            print(f"[{self.account_id}] ❌ 处理订单事件失败: {e}")
    
    def on_trade(self, event: Event) -> None:
        """成交事件处理 - 更新仓位状态"""
        trade: TradeData = event.data
        
        if trade.vt_symbol not in self.supported_symbols:
            return
        
        try:
            # 找到对应的马丁管理器
            for strategy_key, martin_manager in self.martin_managers.items():
                if martin_manager.symbol == trade.vt_symbol:
                    # 🎯 直接更新仓位
                    if trade.direction == Direction.LONG:
                        martin_manager.update_position_on_buy(float(trade.price), float(trade.volume))
                    elif trade.direction == Direction.SHORT:
                        martin_manager.update_position_on_sell(float(trade.volume))
                    break
                    
        except Exception as e:
            print(f"[{self.account_id}] ❌ 处理成交事件失败: {e}")
    
    def on_position(self, event: Event) -> None:
        """仓位事件处理器"""
        position: PositionData = event.data
        if position.vt_symbol in self.supported_symbols:
            print(f"[{self.account_id}] 📊 仓位更新: {position.vt_symbol} 数量={position.volume}")
    
    # =============================================================================
    # 直接操作方法 - 无指令层
    # =============================================================================
    
    def _execute_first_open_order(self, strategy_key: str) -> None:
        """执行首次开仓 - 直接操作"""
        martin_manager = self.martin_managers[strategy_key]
        direction = "LONG" if martin_manager.mode == 1 else "SHORT"

        # 计算开仓参数
        current_price = self._get_current_price(martin_manager.symbol)
        volume = martin_manager.calculate_first_order_params(current_price)
        # 直接发送市价开仓单
        order_id = self._send_market_order(strategy_key, direction, volume, "OPEN")
        #查询订单的成交价
        order = self.main_engine.get_order(order_id)
        if order:
            price = order.price
            #注册新买单
            reference = self.order_manager.generate_order_reference(martin_manager.symbol, "OPEN", direction)
            self.order_manager.register_strategy_buy_order(strategy_key, order_id, reference, price, price,volume, volume, 0)
            self._setup_initial_orders(strategy_key,price,volume,direction)
        else:
            print(f"[{self.account_id}] ❌ 首次开仓失败: {strategy_key}")
            return

    def _setup_initial_orders(self, strategy_key: str, current_price: float,total_volume:float,direction:str) -> None:
        """设置初始订单队列（买单+卖单）"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 1. 设置买单队列
        buy_orders = martin_manager.calculate_buy_orders_queue(current_price, 0, 0)
        buy_success_count = 0
        
        for price, volume,add_sequence,avg_price,total_volume in buy_orders:
            order_id = self._send_limit_order(strategy_key, direction, volume, price, f"BUY_L{add_sequence}")
            if order_id:
                reference = self.order_manager.generate_order_reference(martin_manager.symbol, f"BUY_L{add_sequence}", direction)
                self.order_manager.register_strategy_buy_order(strategy_key, order_id, reference, price, avg_price, volume, total_volume, add_sequence)
                buy_success_count += 1
        
        print(f"[{self.account_id}] ✅ 设置买单队列: {strategy_key} 成功{buy_success_count}/{len(buy_orders)}个")
        
        # 2. 设置卖单
        sell_params = martin_manager.calculate_sell_order_params(current_price,total_volume,0)
        if sell_params:
            sell_price, sell_volume = sell_params
            sell_order_id = self._send_limit_order(strategy_key, Direction.SHORT, sell_volume, sell_price, "PROFIT")
            if sell_order_id:
                reference = self.order_manager.generate_order_reference(martin_manager.symbol, "PROFIT", direction)
                self.order_manager.register_strategy_sell_order(strategy_key, sell_order_id, reference)
                print(f"[{self.account_id}] ✅ 设置卖单: {strategy_key} 价格={sell_price:.6f} 数量={sell_volume:.6f}")
    
    def _handle_buy_order_filled(self, strategy_key: str, order: OrderData) -> None:
        """处理买单成交 - 直接操作"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 获取策略方向
        direction = "LONG" if martin_manager.mode == 1 else "SHORT"

         # 标记买单成交
        result = self.order_manager.mark_buy_order_filled(order.vt_orderid, strategy_key)
        if not result:
            print(f"[{self.account_id}] 警告: 买单成交记录失败 {order.vt_orderid}")
            return
        
        print(f"[{self.account_id}] 买单成交: {strategy_key}  price{buy_info.price} 第{buy_info.add_sequence}次")

        buy_info, max_add_sequence = result
        
        # 如果挂单列表最大加仓次数小于最大加仓次数，则补充挂单
        if max_add_sequence < martin_manager.max_add_count:
            # 计算需要补充的买单
            buy_orders = martin_manager.calculate_buy_orders_queue(buy_info.price, buy_info.add_sequence, max_add_sequence)
            
            success_count = 0
            if buy_orders:
                for price, volume, add_sequence,avg_price,total_volume in buy_orders:
                    action = f"BUY_L{add_sequence}"
                    order_id = self._send_limit_order(strategy_key, Direction.LONG, volume, price, action)   #等修改完逻辑 再修改交易所接口
                    if order_id:
                        # 注册买单
                        reference = self.order_manager.generate_order_reference(martin_manager.symbol, action, direction)
                        self.order_manager.register_strategy_buy_order(strategy_key, order_id, reference, price, volume, total_volume, add_sequence)
                        success_count += 1
                print(f"[{self.account_id}] ✅ 补充买单: {strategy_key} 成功{success_count}/{len(buy_orders)}个")


        # 🎯 直接取消现有卖单
        sell_order_ids = self.order_manager.get_strategy_sell_orders(strategy_key)
        for sell_order_id in sell_order_ids:
            result=self._cancel_order(sell_order_id)
            if result:
                print(f"[{self.account_id}] ✅ 取消卖单: {sell_order_id} 成功")
                #注销卖单
                self.order_manager.unregister_strategy_sell_order(strategy_key, sell_order_id)
            else:
                print(f"[{self.account_id}] ❌ 取消卖单失败: {sell_order_id}")
           
        # 🎯 直接生成新卖单
        sell_order_result = martin_manager.calculate_sell_order_params(buy_info.avg_price,buy_info.total_volume,buy_info.add_sequence)
        if sell_order_result:
            sell_price, sell_volume = sell_order_result
            sell_order_id = self._send_limit_order(strategy_key, Direction.SHORT, sell_volume, sell_price, "PROFIT")
            if sell_order_id:
                reference = self.order_manager.generate_order_reference(martin_manager.symbol, "PROFIT", direction)
                self.order_manager.register_strategy_sell_order(strategy_key, sell_order_id, reference)
                print(f"[{self.account_id}] ✅ 新卖单: {strategy_key} 价格={sell_price:.6f} 数量={sell_volume:.6f}")
        
       
    def _handle_sell_order_filled(self, strategy_key: str, order: OrderData, current_price: float) -> None:
        """处理卖单成交 - 直接操作"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 标记卖单成交
        result = self.order_manager.mark_sell_order_filled(order.vt_orderid)
        if not result:
            print(f"[{self.account_id}] 警告: 卖单成交记录失败 {order.vt_orderid}")
            return
        
        print(f"[{self.account_id}] 卖单成交: {strategy_key}")
        
        # 完全平仓 - 直接取消所有买单并重置
        buy_order_ids, sell_order_ids = self.order_manager.clear_strategy_orders(strategy_key)
        for buy_order_id in buy_order_ids:
            self._cancel_order(buy_order_id)
        for sell_order_id in sell_order_ids:
            self._cancel_order(sell_order_id)
            
        martin_manager.reset_state()
        print(f"[{self.account_id}] ✅ 马丁周期完成: {strategy_key} 开始新周期")
            
        # 直接开始新周期
        self._execute_first_open_order(strategy_key, current_price)
     
    '''
    def _refill_buy_orders(self, strategy_key: str, current_price: float, count: int) -> None:
        """补充买单 - 直接操作"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 获取策略方向
        direction = "LONG" if martin_manager.mode == 1 else "SHORT"
        
        # 计算需要补充的买单
        buy_orders = martin_manager.calculate_buy_orders_queue(current_price, 0, 0)
        refill_orders = buy_orders[:count]  # 只取需要的数量
        
        success_count = 0
        for price, volume, level in refill_orders:
            order_id = self._send_limit_order(strategy_key, Direction.LONG, volume, price, f"REFILL_L{level}")
            if order_id:
                # 注册买单
                reference = self.order_manager.generate_order_reference(martin_manager.symbol, f"REFILL_L{level}", direction)
                self.order_manager.register_strategy_buy_order(strategy_key, order_id, reference, price, volume, level)
                success_count += 1
        
        print(f"[{self.account_id}] ✅ 补充买单: {strategy_key} 成功{success_count}/{count}个")
    '''
    def _send_market_order(self, strategy_key: str, direction: Direction, volume: float, action: str) -> Optional[str]:
        """发送市价单"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 获取策略方向
        strategy_direction = "LONG" if martin_manager.mode == 1 else "SHORT"
        
        order_req = OrderRequest(
            symbol=martin_manager.symbol,
            exchange=Exchange.OKX,
            direction=direction,
            type=OrderType.MARKET,
            volume=Decimal(str(volume)),
            reference=self.order_manager.generate_order_reference(martin_manager.symbol, action, strategy_direction)
        )
        
        return self._send_order(order_req)
    
    def _send_limit_order(self, strategy_key: str, direction: Direction, volume: float, price: float, action: str) -> Optional[str]:
        """发送限价单"""
        martin_manager = self.martin_managers[strategy_key]
        
        # 获取策略方向
        strategy_direction = "LONG" if martin_manager.mode == 1 else "SHORT"
        
        order_req = OrderRequest(
            symbol=martin_manager.symbol,
            exchange=Exchange.OKX,
            direction=direction,
            type=OrderType.LIMIT,
            volume=Decimal(str(volume)),
            price=Decimal(str(price)),
            reference=self.order_manager.generate_order_reference(martin_manager.symbol, action, strategy_direction)
        )
        
        return self._send_order(order_req)
    
    def _send_order(self, order_req: OrderRequest) -> Optional[str]:
        """发送订单到交易所"""
        try:
            # 构造完整的交易对名称
            full_symbol = f"{order_req.symbol}.{order_req.exchange.value}"
            contract = self.main_engine.get_contract(full_symbol)
            
            if not contract:
                print(f"[{self.account_id}] ❌ 无法获取合约信息: {full_symbol}")
                return None
            
            vt_orderid = self.main_engine.send_order(order_req, contract.gateway_name)
            
            if vt_orderid:
                direction_text = "买入" if order_req.direction == Direction.LONG else "卖出"
                print(f"[{self.account_id}] ✅ 发送订单: {direction_text} 价格={order_req.price} 数量={order_req.volume}")
                return vt_orderid
            else:
                print(f"[{self.account_id}] ❌ 发送订单失败")
                return None
                
        except Exception as e:
            print(f"[{self.account_id}] ❌ 发送订单异常: {e}")
            return None
    
    def _cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        try:
            order = self.main_engine.get_order(order_id)
            if order and order.is_active():
                cancel_req = order.create_cancel_request()
                self.main_engine.cancel_order(cancel_req, order.gateway_name)
                print(f"[{self.account_id}] ✅ 撤销订单: {order_id}")
                return True
        except Exception as e:
            print(f"[{self.account_id}] ❌ 撤销订单失败 {order_id}: {e}")
        return False
    
    # 其他辅助方法
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        try:
            tick = self.main_engine.get_tick(symbol)
            if tick and tick.last_price:
                return float(tick.last_price)
        except Exception:
            pass
        return None
    
    def _get_contract_info(self, symbol: str) -> Optional[ContractData]:
        """获取合约信息"""
        try:
            return self.main_engine.get_contract(symbol)
        except Exception:
            pass
        return None
    
    def _register_events(self) -> None:
        """注册事件监听"""
        self.event_engine.register(EVENT_ORDER, self.on_order)
        self.event_engine.register(EVENT_TRADE, self.on_trade)
        self.event_engine.register(EVENT_POSITION, self.on_position)
        print(f"[{self.account_id}] 事件监听已注册")
    
    def _unregister_events(self) -> None:
        """注销事件监听"""
        try:
            self.event_engine.unregister(EVENT_ORDER, self.on_order)
            self.event_engine.unregister(EVENT_TRADE, self.on_trade)
            self.event_engine.unregister(EVENT_POSITION, self.on_position)
            print(f"[{self.account_id}] 事件监听已注销")
        except Exception as e:
            print(f"[{self.account_id}] 注销事件监听失败: {e}")
    
    def _start_timer(self) -> None:
        """启动定时任务"""
        if self.timer_thread and self.timer_thread.is_alive():
            return
        
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()
        print(f"[{self.account_id}] 定时器已启动 (间隔: {self.timer_interval}秒)")
    
    def _stop_timer(self) -> None:
        """停止定时任务"""
        if self.timer_thread and self.timer_thread.is_alive():
            self.timer_thread.join(timeout=5)
        print(f"[{self.account_id}] 定时器已停止")
    
    def _timer_loop(self) -> None:
        """定时任务循环"""
        while self.active:
            try:
                # 健康检查
                self._health_check()
                
                # 等待下一次执行
                time.sleep(self.timer_interval)
                
            except Exception as e:
                print(f"[{self.account_id}] ❌ 定时任务异常: {e}")
                time.sleep(5)
    
    def _health_check(self) -> None:
        """健康检查"""
        try:
            for strategy_key, martin_manager in self.martin_managers.items():
                health_status = martin_manager.get_health_status()
                
                if not health_status['is_healthy']:
                    print(f"[{self.account_id}] ⚠️ 策略健康检查异常: {strategy_key}")
                
        except Exception as e:
            print(f"[{self.account_id}] ❌ 健康检查失败: {e}")

    def _set_contract_leverage(self, symbol: str, lever: int, margin_mode: str) -> None:
        """设置合约杠杆倍数"""
        try:
            # 获取网关实例
            gateway = self.main_engine.get_gateway(self.config.gateway_name)
            if not gateway or not hasattr(gateway, 'set_leverage'):
                print(f"[{self.account_id}] 警告: 网关不支持杠杆设置")
                return
            
            # 设置杠杆
            gateway.set_leverage(symbol, lever, margin_mode)
            print(f"[{self.account_id}] ✅ 设置杠杆: {symbol} {lever}倍 {margin_mode}模式")
            
        except Exception as e:
            print(f"[{self.account_id}] ❌ 设置杠杆失败: {e}")


# =============================================================================
# 使用示例
# =============================================================================

if __name__ == "__main__":
    """
    单账户执行器使用示例
    """
    from howtrader.event import EventEngine
    from howtrader.trader.engine import MainEngine
    
    # 创建引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 账户配置
    account_config = AccountConfig(
        account_id="TEST_ACCOUNT_001",
        api_key="your_api_key",
        api_secret="your_api_secret", 
        api_passphrase="your_passphrase",
        gateway_name="OKX",
        test_mode=True,
        supported_symbols=["BTC-USDT-SWAP.OKX", "ETH-USDT-SWAP.OKX"]
    )
    
    # 马丁策略配置
   
    martin_config={
    'opp_ratio': 0.0025,
    'profit_target': 0.0055,
    'lever': 10,
    'initial_margin':100000,
    'adding_number': 25,
    'amount_multiplie': 1.13,
    'price_multiple': 1.10,
    'mode': 1
     }
    
    try:
        print("🚀 启动买单队列马丁策略测试")
        
        # 创建执行器
        executor = SingleAccountExecutor(account_config, main_engine, event_engine)
        
        # 启动执行器
        executor.start()
        
        # 添加BTC做多策略
        if executor.add_martin_strategy("BTC-USDT-SWAP.OKX", 1, martin_config):
            print("✅ BTC做多马丁策略添加成功")
        
        print("\n🔄 执行器运行中...")
        print("按 Ctrl+C 停止")
        
        # 主循环
        while True:
            time.sleep(30)
        
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号...")
        
    except Exception as e:
        print(f"❌ 运行异常: {e}")
        
    finally:
        if 'executor' in locals():
            executor.stop()
        print("🏁 执行器已停止")