#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
账户执行器 (AccountExecutor)
============================

基于HowTrader框架的事件驱动账户执行器
- 接收趋势策略引擎信号(未来再优化趋势与马丁策略结合,目标版本马丁策略与趋势策略独立操作，该执行器接受趋势策略引擎信号并执行下单动作但与马丁无关)
- 管理多交易对马丁策略
- 订单归属识别和管理
- 状态持久化和恢复
- 风险控制和监控

作者：基于HowTrader架构设计
版本：v1.0
"""

import time
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from decimal import Decimal
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from howtrader.event import Event, EventEngine
from howtrader.trader.engine import MainEngine
from howtrader.trader.object import (
    OrderData, TradeData, PositionData, ContractData,
    OrderRequest, CancelRequest, SubscribeRequest
)
from howtrader.trader.event import EVENT_ORDER, EVENT_TRADE, EVENT_POSITION
from howtrader.trader.constant import Direction, Offset, Status, OrderType
from howtrader.trader.utility import extract_vt_symbol


# =============================================================================
# 事件定义
# =============================================================================
EVENT_TREND_SIGNAL = "eTrendSignal"           # 趋势信号事件
EVENT_ACCOUNT_STATUS = "eAccountStatus"       # 账户状态事件
EVENT_MARTIN_UPDATE = "eMartinUpdate"         # 马丁策略更新事件


# =============================================================================
# 数据结构定义 - 更新马丁策略键值
# =============================================================================

class ExecutionMode(Enum):
    """执行模式枚举"""
    NORMAL = "normal"                    # 正常执行
    POSITION_ONLY = "position_only"     # 仅管理现有仓位
    EMERGENCY_EXIT = "emergency_exit"   # 紧急退出
    SUSPENDED = "suspended"              # 暂停执行


class OrderCategory(Enum):
    """订单类别枚举"""
    MARTIN = "martin"                    # 马丁策略订单
    TREND = "trend"                      # 趋势策略订单
    MANUAL = "manual"                    # 手动订单
    UNKNOWN = "unknown"                  # 未知订单


@dataclass
class TrendSignal:
    """趋势信号数据结构"""
    symbol: str
    timeframes: Dict[str, Dict]          # 多时间框架数据
    overall_direction: int               # 总体方向: 1上涨, -1下跌, 0震荡
    overall_strength: float              # 总体强度: 0-1
    confidence: float                    # 信号可信度: 0-1
    timestamp: datetime
    source: str


@dataclass
class MartinState:
    """马丁策略状态"""
    symbol: str
    mode: int                           # 1=做多, 2=做空
    avg_price: float                    # 平均成本价
    position_size: float                # 仓位大小
    add_count: int                      # 加仓次数
    total_margin_used: float            # 已使用保证金
    active_orders: List[str]            # 活跃订单ID列表
    execution_mode: ExecutionMode       # 执行模式
    last_update: datetime


# =============================================================================
# 订单管理器
# =============================================================================

class OrderManager:
    """
    订单管理器
    
    功能：
    1. 订单归属识别
    2. 订单状态追踪
    3. 订单ID生成和管理
    """
    
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.order_sequence = 0
        self.order_mapping: Dict[str, Dict] = {}  # order_id -> order_info
        self.symbol_orders: Dict[str, Set[str]] = {}  # symbol -> order_ids
        self.category_orders: Dict[OrderCategory, Set[str]] = {}  # category -> order_ids
        
        # 初始化类别集合
        for category in OrderCategory:
            self.category_orders[category] = set()
    
    def generate_order_reference(self, symbol: str, category: OrderCategory, action: str) -> str:
        """
        生成订单reference标识
        
        格式: {category}_{account_id}_{symbol}_{action}_{sequence}
        示例: MARTIN_ACC001_BTCUSDT_OPEN_001
        """
        self.order_sequence += 1
        
        # 清理symbol中的特殊字符
        clean_symbol = symbol.replace('-', '').replace('.', '').replace('_', '')
        
        reference = f"{category.value.upper()}_{self.account_id}_{clean_symbol}_{action}_{self.order_sequence:04d}"
        return reference
    
    def register_order(self, order_id: str, symbol: str, category: OrderCategory, 
                      action: str, reference: str) -> None:
        """注册订单信息"""
        order_info = {
            'order_id': order_id,
            'symbol': symbol,
            'category': category,
            'action': action,
            'reference': reference,
            'timestamp': datetime.now(),
            'status': 'registered'
        }
        
        self.order_mapping[order_id] = order_info
        
        # 添加到对应集合
        if symbol not in self.symbol_orders:
            self.symbol_orders[symbol] = set()
        self.symbol_orders[symbol].add(order_id)
        self.category_orders[category].add(order_id)
    
    def classify_order(self, order: OrderData) -> OrderCategory:
        """根据订单reference分类订单"""
        if order.reference.startswith('MARTIN_'):
            return OrderCategory.MARTIN
        elif order.reference.startswith('TREND_'):
            return OrderCategory.TREND
        elif order.reference.startswith('MANUAL_'):
            return OrderCategory.MANUAL
        else:
            return OrderCategory.UNKNOWN
    
    def is_my_order(self, order: OrderData) -> bool:
        """判断是否是本账户的订单"""
        return (order.vt_orderid in self.order_mapping or 
                order.reference.find(f"_{self.account_id}_") > 0)
    
    def get_active_orders_by_symbol(self, symbol: str) -> List[str]:
        """获取指定交易对的活跃订单"""
        return list(self.symbol_orders.get(symbol, set()))
    
    def remove_order(self, order_id: str) -> None:
        """移除订单记录"""
        if order_id in self.order_mapping:
            order_info = self.order_mapping[order_id]
            symbol = order_info['symbol']
            category = order_info['category']
            
            # 从各个集合中移除
            del self.order_mapping[order_id]
            if symbol in self.symbol_orders:
                self.symbol_orders[symbol].discard(order_id)
            self.category_orders[category].discard(order_id)


# =============================================================================
# 马丁策略管理器 - 修复API调用和数据类型
# =============================================================================

class MartinManager:
    """
    马丁策略管理器
    
    功能：
    1. 单交易对单模式马丁策略执行
    2. 仓位和成本计算
    3. 加仓和止盈逻辑
    4. 与趋势信号协调
    """
    
    def __init__(self, account_id: str, symbol: str, mode: int, config: dict, 
                 order_manager: OrderManager, main_engine: MainEngine):
        self.account_id = account_id
        self.symbol = symbol
        self.mode = mode  # 1=做多, 2=做空
        self.config = config
        self.order_manager = order_manager
        self.main_engine = main_engine
        
        # 马丁策略参数
        self.lever = config.get('lever', 10)                       # 杠杆倍数
        self.first_margin = config.get('first_margin', 50.0)       # 首次保证金
        self.first_margin_add = config.get('first_margin_add', 50.0)  # 加仓保证金
        self.adding_number = config.get('adding_number', 20)       # 最大加仓次数
        self.amount_multiplier = config.get('amount_multiplier', 1.2)  # 加仓倍数
        self.profit_target = config.get('profit_target', 0.01)     # 止盈比例
        self.opp_ratio = config.get('opp_ratio', 0.025)           # 加仓触发比例
        self.max_total_margin = config.get('max_total_margin', 1000.0)  # 最大保证金
        
        # 策略状态
        self.state = MartinState(
            symbol=symbol,
            mode=self.mode,
            avg_price=0.0,
            position_size=0.0,
            add_count=0,
            total_margin_used=0.0,
            active_orders=[],
            # 设置马丁策略的执行模式为正常模式（NORMAL）
            execution_mode=ExecutionMode.NORMAL,
            last_update=datetime.now()
        )
        
        # 合约信息
        self.contract: Optional[ContractData] = None
        self.price_tick = Decimal("0.01")
        self.size_tick = Decimal("0.001")
        
        # 获取合约信息
        self._load_contract_info()
    
    def _load_contract_info(self) -> None:
        """加载合约信息"""
        try:
            # 使用安全方法获取合约信息
            get_contract = getattr(self.main_engine, 'get_contract', None)
            if get_contract:
                self.contract = get_contract(self.symbol)
                if self.contract:
                    self.price_tick = self.contract.pricetick
                    # 最小合约size应根据交易所返回的数据设置
                    self.size_tick = getattr(self.contract, 'min_volume', Decimal("0.001"))
                    print(f"合约信息: symbol={self.symbol}, name={getattr(self.contract, 'name', '未知')}, price_tick={self.price_tick}, size_tick={self.size_tick}")
                else:
                    print(f"警告: 无法获取合约信息 {self.symbol}, 使用默认参数")
            else:
                print(f"警告: MainEngine 不支持 get_contract 方法，使用默认合约参数")
        except Exception as e:
            print(f"加载合约信息失败: {e}")
            print(f"使用默认合约参数: price_tick={self.price_tick}, size_tick={self.size_tick}")
    
    def set_execution_mode(self, mode: ExecutionMode) -> None:
        """设置执行模式"""
        self.state.execution_mode = mode
        self.state.last_update = datetime.now()
        print(f"[{self.symbol}] 马丁策略执行模式变更为: {mode.value}")
    
    def on_order_update(self, order: OrderData) -> None:
        """处理订单更新"""
        if not self.order_manager.is_my_order(order):
            return
        
        category = self.order_manager.classify_order(order)
        if category != OrderCategory.MARTIN:
            return
        
        # 更新活跃订单列表
        if order.is_active():
            if order.vt_orderid not in self.state.active_orders:
                self.state.active_orders.append(order.vt_orderid)
        else:
            if order.vt_orderid in self.state.active_orders:
                self.state.active_orders.remove(order.vt_orderid)
        
        # 处理订单成交
        if order.status == Status.ALLTRADED:
            if order.direction == Direction.LONG:
                # 买单成交，更新加仓计数
                self.state.add_count += 1
                print(f"[{self.symbol}] 马丁买单成交: 价格={order.price}, 数量={order.volume}, 第{self.state.add_count}次")
            elif order.direction == Direction.SHORT:
                # 卖单成交，可能完成一轮马丁
                print(f"[{self.symbol}] 马丁卖单成交: 价格={order.price}, 数量={order.volume}")
        
        self.state.last_update = datetime.now()
    
    def on_trade_update(self, trade: TradeData) -> None:
        """处理成交更新"""
        if trade.vt_symbol != self.symbol:
            return
        
        if trade.direction == Direction.LONG:
            # 买入成交，更新平均成本
            if self.state.position_size > 0:
                total_cost = self.state.avg_price * self.state.position_size + float(trade.price) * float(trade.volume)
                self.state.position_size += float(trade.volume)
                self.state.avg_price = total_cost / self.state.position_size
            else:
                self.state.avg_price = float(trade.price)
                self.state.position_size = float(trade.volume)
        
        elif trade.direction == Direction.SHORT:
            # 卖出成交，减少仓位
            self.state.position_size -= float(trade.volume)
            
            if self.state.position_size <= float(self.size_tick):
                # 基本平完仓，重置马丁状态
                self._reset_martin_state()
                print(f"[{self.symbol}] 马丁策略完成一轮，状态已重置")
        
        self.state.last_update = datetime.now()
    
    def calculate_next_action(self, current_price: float, trend_signal: Optional[TrendSignal] = None) -> Optional[OrderRequest]:
        """
        计算下一步马丁动作
        
        返回OrderRequest或None
        """
        # 检查执行模式
        if self.state.execution_mode == ExecutionMode.SUSPENDED:
            return None
        elif self.state.execution_mode == ExecutionMode.EMERGENCY_EXIT:
            return self._create_emergency_exit_order(current_price)
        
        # 无仓位时，考虑首次开仓
        if abs(self.state.position_size) <= float(self.size_tick):
            return self._calculate_first_position_order(current_price, trend_signal)
        
        # 有仓位时，考虑加仓或止盈
        return self._calculate_position_management_order(current_price, trend_signal)
    
    def _calculate_first_position_order(self, current_price: float, trend_signal: Optional[TrendSignal]) -> Optional[OrderRequest]:
        """计算首次开仓订单"""
        if self.state.execution_mode == ExecutionMode.POSITION_ONLY:
            return None
        
        # 如果有趋势信号，检查是否适合开仓
        if trend_signal and self._should_skip_open_by_trend(trend_signal):
            return None
        
        # 计算开仓数量
        position_value = self.first_margin * self.lever
        volume = position_value / current_price
        volume = self._round_to_size_tick(volume)
        
        # 生成订单reference
        action = "OPEN"
        reference = self.order_manager.generate_order_reference(self.symbol, OrderCategory.MARTIN, action)
        
        # 创建订单请求
        symbol, exchange = extract_vt_symbol(self.symbol)
        
        if self.mode == 1:  # 做多
            direction = Direction.LONG
            offset = Offset.OPEN
        else:  # 做空
            direction = Direction.SHORT
            offset = Offset.OPEN
        
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            offset=offset,
            type=OrderType.LIMIT,
            price=Decimal(str(current_price)),
            volume=Decimal(str(volume)),
            reference=reference
        )
        
        return req
    
    def _calculate_position_management_order(self, current_price: float, trend_signal: Optional[TrendSignal]) -> Optional[OrderRequest]:
        """计算仓位管理订单（加仓或止盈）"""
        
        # 1. 检查是否需要止盈
        profit_order = self._calculate_profit_order(current_price)
        if profit_order:
            return profit_order
        
        # 2. 检查是否需要加仓
        if self.state.execution_mode != ExecutionMode.POSITION_ONLY:
            add_order = self._calculate_add_position_order(current_price, trend_signal)
            if add_order:
                return add_order
        
        return None
    
    def _calculate_profit_order(self, current_price: float) -> Optional[OrderRequest]:
        """计算止盈订单"""
        if self.state.avg_price <= 0 or self.state.position_size <= 0:
            return None
        
        # 检查是否已有止盈单
        if len(self.state.active_orders) > 0:
            return None
        
        # 计算止盈价格
        if self.mode == 1:  # 做多
            profit_price = self.state.avg_price * (1 + self.profit_target)
        else:  # 做空
            profit_price = self.state.avg_price * (1 - self.profit_target)
        
        # 只有价格达到止盈条件时才挂单
        if self.mode == 1 and current_price < profit_price:
            return None
        elif self.mode == 2 and current_price > profit_price:
            return None
        
        # 计算止盈数量
        profit_volume = self.state.position_size * 0.9  # 保留10%仓位
        profit_volume = self._round_to_size_tick(profit_volume)
        
        if profit_volume < float(self.size_tick):
            return None
        
        # 生成订单
        action = "PROFIT"
        reference = self.order_manager.generate_order_reference(self.symbol, OrderCategory.MARTIN, action)
        symbol, exchange = extract_vt_symbol(self.symbol)
        
        if self.mode == 1:  # 做多平仓
            direction = Direction.SHORT
            offset = Offset.CLOSE
        else:  # 做空平仓
            direction = Direction.LONG
            offset = Offset.CLOSE
        
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            offset=offset,
            type=OrderType.LIMIT,
            price=Decimal(str(profit_price)),
            volume=Decimal(str(profit_volume)),
            reference=reference
        )
        
        return req
    
    def _calculate_add_position_order(self, current_price: float, trend_signal: Optional[TrendSignal]) -> Optional[OrderRequest]:
        """计算加仓订单"""
        
        # 检查加仓条件
        if (self.state.add_count >= self.adding_number or 
            self.state.total_margin_used >= self.max_total_margin):
            return None
        
        # 检查价格偏离是否足够
        if self.state.avg_price <= 0:
            return None
        
        if self.mode == 1:  # 做多模式，价格下跌时加仓
            price_deviation = (self.state.avg_price - current_price) / self.state.avg_price
        else:  # 做空模式，价格上涨时加仓
            price_deviation = (current_price - self.state.avg_price) / self.state.avg_price
        
        if price_deviation < self.opp_ratio:
            return None
        
        # 如果有趋势信号，检查是否适合加仓
        if trend_signal and self._should_skip_add_by_trend(trend_signal):
            return None
        
        # 计算加仓数量
        add_margin = self.first_margin_add * (self.amount_multiplier ** self.state.add_count)
        if self.state.total_margin_used + add_margin > self.max_total_margin:
            return None
        
        position_value = add_margin * self.lever
        add_volume = position_value / current_price
        add_volume = self._round_to_size_tick(add_volume)
        
        # 生成加仓订单
        action = f"ADD_{self.state.add_count + 1}"
        reference = self.order_manager.generate_order_reference(self.symbol, OrderCategory.MARTIN, action)
        symbol, exchange = extract_vt_symbol(self.symbol)
        
        if self.mode == 1:  # 做多加仓
            direction = Direction.LONG
            offset = Offset.OPEN
        else:  # 做空加仓
            direction = Direction.SHORT
            offset = Offset.OPEN
        
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            offset=offset,
            type=OrderType.LIMIT,
            price=Decimal(str(current_price)),
            volume=Decimal(str(add_volume)),
            reference=reference
        )
        
        return req
    '''
    def _should_skip_open_by_trend(self, trend_signal: TrendSignal) -> bool:
        """根据趋势信号判断是否跳过开仓"""
        if self.mode == 1 and trend_signal.overall_direction < 0 and trend_signal.overall_strength > 0.7:
            # 做多模式但强烈下跌趋势
            return True
        elif self.mode == 2 and trend_signal.overall_direction > 0 and trend_signal.overall_strength > 0.7:
            # 做空模式但强烈上涨趋势
            return True
        return False
    
    def _should_skip_add_by_trend(self, trend_signal: TrendSignal) -> bool:
        """根据趋势信号判断是否跳过加仓"""
        # 类似开仓逻辑，但可以设置不同的阈值
        return self._should_skip_open_by_trend(trend_signal)
    '''
    def _create_emergency_exit_order(self, current_price: float) -> Optional[OrderRequest]:
        """创建紧急退出订单"""
        if self.state.position_size <= float(self.size_tick):
            return None
        
        action = "EMERGENCY_EXIT"
        reference = self.order_manager.generate_order_reference(self.symbol, OrderCategory.MARTIN, action)
        symbol, exchange = extract_vt_symbol(self.symbol)
        
        if self.mode == 1:  # 做多紧急平仓
            direction = Direction.SHORT
            offset = Offset.CLOSE
        else:  # 做空紧急平仓
            direction = Direction.LONG
            offset = Offset.CLOSE
        
        # 使用TAKER订单类型快速成交（类似市价单）
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            offset=offset,
            type=OrderType.TAKER,  # 使用TAKER而不是MARKET
            price=Decimal(str(current_price)),
            volume=Decimal(str(self.state.position_size)),
            reference=reference
        )
        
        return req
    
    def _reset_martin_state(self) -> None:
        """重置马丁策略状态"""
        self.state.avg_price = 0.0
        self.state.position_size = 0.0
        self.state.add_count = 0
        self.state.total_margin_used = 0.0
        self.state.active_orders.clear()
        self.state.execution_mode = ExecutionMode.NORMAL
        self.state.last_update = datetime.now()
    
    def _round_to_size_tick(self, volume: float) -> float:
        """调整到合约最小单位"""
        return max(float(self.size_tick), round(volume / float(self.size_tick)) * float(self.size_tick))
    
    def get_state(self) -> MartinState:
        """获取策略状态"""
        return self.state

'''
# =============================================================================
# 策略协调器
# =============================================================================

class StrategyCoordinator:
    """
    策略协调器
    
    功能：
    1. 协调趋势信号和马丁策略
    2. 决策执行模式切换
    3. 管理策略优先级
    """
    
    def __init__(self):
        self.coordination_history: List[Dict] = []
    
    def coordinate_strategies(self, symbol: str, trend_signal: TrendSignal, 
                            martin_manager: MartinManager) -> None:
        """
        协调策略执行
        
        核心协调逻辑：
        - 趋势有利：正常执行马丁策略
        - 趋势不利但不强烈：仅管理现有仓位
        - 趋势强烈不利：考虑紧急退出
        """
        
        old_mode = martin_manager.state.execution_mode
        new_mode = self._calculate_execution_mode(trend_signal, martin_manager)
        
        if new_mode != old_mode:
            martin_manager.set_execution_mode(new_mode)
            
            # 记录协调决策
            decision = {
                'timestamp': datetime.now(),
                'symbol': symbol,
                'trend_direction': trend_signal.overall_direction,
                'trend_strength': trend_signal.overall_strength,
                'martin_mode': martin_manager.mode,
                'old_execution_mode': old_mode.value,
                'new_execution_mode': new_mode.value,
                'reason': self._get_coordination_reason(trend_signal, martin_manager, new_mode)
            }
            
            self.coordination_history.append(decision)
            print(f"[策略协调] {symbol}: {old_mode.value} -> {new_mode.value} | 原因: {decision['reason']}")
    
    def _calculate_execution_mode(self, trend_signal: TrendSignal, martin_manager: MartinManager) -> ExecutionMode:
        """计算执行模式"""
        
        trend_direction = trend_signal.overall_direction
        trend_strength = trend_signal.overall_strength
        confidence = trend_signal.confidence
        martin_mode = martin_manager.mode
        
        # 低可信度信号，保持正常执行
        if confidence < 0.6:
            return ExecutionMode.NORMAL
        
        if martin_mode == 1:  # 马丁做多模式
            if trend_direction >= 0:  # 趋势向上或震荡
                return ExecutionMode.NORMAL
            elif trend_direction < 0 and trend_strength < 0.7:  # 轻微下跌
                return ExecutionMode.NORMAL
            elif trend_direction < 0 and trend_strength < 0.9:  # 明显下跌
                return ExecutionMode.POSITION_ONLY
            else:  # 强烈下跌
                return ExecutionMode.EMERGENCY_EXIT
                
        elif martin_mode == 2:  # 马丁做空模式
            if trend_direction <= 0:  # 趋势向下或震荡
                return ExecutionMode.NORMAL
            elif trend_direction > 0 and trend_strength < 0.7:  # 轻微上涨
                return ExecutionMode.NORMAL
            elif trend_direction > 0 and trend_strength < 0.9:  # 明显上涨
                return ExecutionMode.POSITION_ONLY
            else:  # 强烈上涨
                return ExecutionMode.EMERGENCY_EXIT
        
        return ExecutionMode.NORMAL
    
    def _get_coordination_reason(self, trend_signal: TrendSignal, martin_manager: MartinManager, 
                               new_mode: ExecutionMode) -> str:
        """获取协调原因说明"""
        trend_direction = trend_signal.overall_direction
        trend_strength = trend_signal.overall_strength
        martin_mode = martin_manager.mode
        
        direction_text = "上涨" if trend_direction > 0 else ("下跌" if trend_direction < 0 else "震荡")
        strength_text = "强" if trend_strength > 0.8 else ("中" if trend_strength > 0.5 else "弱")
        mode_text = "做多" if martin_mode == 1 else "做空"
        
        if new_mode == ExecutionMode.NORMAL:
            return f"趋势{direction_text}({strength_text})，与{mode_text}马丁策略匹配，正常执行"
        elif new_mode == ExecutionMode.POSITION_ONLY:
            return f"趋势{direction_text}({strength_text})，与{mode_text}马丁策略冲突，仅管理仓位"
        elif new_mode == ExecutionMode.EMERGENCY_EXIT:
            return f"趋势{direction_text}({strength_text})，与{mode_text}马丁策略严重冲突，紧急退出"
        else:
            return f"其他原因"

'''
# =============================================================================
# 马丁策略状态恢复器 - 基于交易所数据的混合恢复方案
# =============================================================================

@dataclass
class MartinRecoveryState:
    """马丁策略恢复状态"""
    symbol: str
    mode: int                           # 1=做多, 2=做空
    total_position: float               # 交易所实际仓位
    avg_cost_price: float               # 交易所平均成本价
    add_count: int                      # 从订单历史分析的加仓次数
    active_orders: List[str]            # 当前活跃订单
    recovery_action: str                # 建议恢复动作
    confidence: float                   # 恢复可信度 0-1
    exchange_verified: bool             # 是否经过交易所验证
    analysis_details: Dict              # 分析详情


class MartinStateRecovery:
    """
    马丁策略状态恢复器
    
    采用混合方案：
    1. 优先使用交易所权威数据（仓位、平均价）
    2. 通过订单分析补充策略细节（加仓次数）
    3. 交叉验证确保数据一致性
    4. 智能决策恢复动作
    """
    
    def __init__(self, main_engine: MainEngine, account_id: str):
        self.main_engine = main_engine
        self.account_id = account_id
        self.gateway_name = "OKX"  # 可以根据需要配置
        
    def recover_martin_strategies(self, symbols: List[str], modes: Optional[List[int]] = None, 
                                lookback_hours: int = 24) -> Dict[str, MartinRecoveryState]:
        """
        恢复马丁策略状态
        
        参数:
        - symbols: 需要恢复的交易对列表
        - modes: 对应的模式列表，如果为None则尝试恢复所有模式
        - lookback_hours: 订单历史回看时间
        
        返回: {strategy_key: MartinRecoveryState}
        """
        print(f"[马丁恢复] 开始恢复马丁策略状态...")
        print(f"[马丁恢复] 交易对: {symbols}")
        print(f"[马丁恢复] 回看时间: {lookback_hours}小时")
        
        recovery_results = {}
        
        # 1. 主动查询交易所最新数据
        self._refresh_exchange_data()
        
        for symbol in symbols:
            if modes is None:
                # 尝试恢复所有模式
                test_modes = [1, 2]  # 做多和做空
            else:
                test_modes = modes
            
            for mode in test_modes:
                strategy_key = f"{symbol}_M{mode}"
                
                try:
                    recovery_state = self._recover_single_strategy(symbol, mode, lookback_hours)
                    if recovery_state:
                        recovery_results[strategy_key] = recovery_state
                        print(f"[马丁恢复] ✅ {strategy_key} 恢复成功")
                    else:
                        print(f"[马丁恢复] ⚠️ {strategy_key} 无需恢复或数据不足")
                        
                except Exception as e:
                    print(f"[马丁恢复] ❌ {strategy_key} 恢复失败: {e}")
        
        print(f"[马丁恢复] 恢复完成，成功恢复 {len(recovery_results)} 个策略")
        return recovery_results
    
    def _refresh_exchange_data(self) -> None:
        """刷新交易所数据"""
        try:
            print("[马丁恢复] 刷新交易所数据...")
            
            # 使用更安全的方法查询数据
            try:
                # 通过主引擎的网关查询
                for gateway_name in ['OKX', 'BINANCE']:
                    if hasattr(self.main_engine, 'get_gateway'):
                        gateway = self.main_engine.get_gateway(gateway_name)
                        if gateway:
                            # 查询仓位
                            if hasattr(gateway, 'query_position'):
                                gateway.query_position()
                            # 查询账户
                            if hasattr(gateway, 'query_account'):
                                gateway.query_account()
                            break  # 找到有效网关就退出
            except Exception:
                # 如果网关方法失败，跳过
                pass
            
            # 等待数据更新
            import time
            time.sleep(2)
            
            print("[马丁恢复] 交易所数据刷新完成")
            
        except Exception as e:
            print(f"[马丁恢复] 刷新交易所数据失败: {e}")
    
    def _recover_single_strategy(self, symbol: str, mode: int, lookback_hours: int) -> Optional[MartinRecoveryState]:
        """恢复单个马丁策略"""
        mode_text = "做多" if mode == 1 else "做空"
        print(f"[马丁恢复] 分析 {symbol} {mode_text} 策略...")
        
        # 1. 获取交易所仓位数据 (最权威)
        exchange_position = self._get_exchange_position(symbol)
        
        # 2. 分析订单历史
        order_analysis = self._analyze_martin_orders(symbol, mode, lookback_hours)
        
        # 3. 获取活跃订单
        active_orders = self._get_active_martin_orders(symbol, mode)
        
        # 4. 数据验证和一致性检查
        validation_result = self._validate_recovery_data(
            symbol, mode, exchange_position, order_analysis, active_orders
        )
        
        # 5. 决策恢复动作
        if not validation_result['has_position'] and not validation_result['has_orders']:
            # 无仓位无订单，不需要恢复
            return None
        
        recovery_action, confidence = self._determine_recovery_action(
            symbol, mode, exchange_position, order_analysis, active_orders, validation_result
        )
        
        # 6. 构建恢复状态
        recovery_state = MartinRecoveryState(
            symbol=symbol,
            mode=mode,
            total_position=exchange_position['volume'] if exchange_position else 0.0,
            avg_cost_price=exchange_position['avg_price'] if exchange_position else 0.0,
            add_count=order_analysis['add_count'],
            active_orders=active_orders,
            recovery_action=recovery_action,
            confidence=confidence,
            exchange_verified=exchange_position is not None,
            analysis_details={
                'exchange_position': exchange_position,
                'order_analysis': order_analysis,
                'validation': validation_result
            }
        )
        
        self._log_recovery_details(recovery_state)
        return recovery_state
    
    def _get_exchange_position(self, symbol: str) -> Optional[Dict]:
        """从交易所获取仓位数据"""
        try:
            # 尝试多种方式获取仓位
            position_keys = [
                f"{symbol}.NET",  # 净持仓模式
                f"{symbol}.{Direction.NET.value}",
                symbol
            ]
            
            for key in position_keys:
                # 使用getattr安全调用可能不存在的方法
                get_position = getattr(self.main_engine, 'get_position', None)
                if get_position:
                    position = get_position(key)
                    if position and abs(position.volume) > 1e-8:  # 有效仓位
                        return {
                            'volume': float(position.volume),
                            'avg_price': float(position.price) if position.price > 0 else 0.0,
                            'pnl': float(getattr(position, 'pnl', 0.0)),
                            'source': 'exchange_cache'
                        }
            
            # 如果缓存中没有，尝试查询所有仓位
            get_all_positions = getattr(self.main_engine, 'get_all_positions', None)
            if get_all_positions:
                all_positions = get_all_positions()
                for pos in all_positions:
                    if pos.vt_symbol == symbol and abs(pos.volume) > 1e-8:
                        return {
                            'volume': float(pos.volume),
                            'avg_price': float(pos.price) if pos.price > 0 else 0.0,
                            'pnl': float(getattr(pos, 'pnl', 0.0)),
                            'source': 'exchange_query'
                        }
            
            return None
            
        except Exception as e:
            print(f"[马丁恢复] 获取交易所仓位失败 {symbol}: {e}")
            return None
    
    def _analyze_martin_orders(self, symbol: str, mode: int, lookback_hours: int) -> Dict:
        """分析马丁策略订单历史"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=lookback_hours)
            mode_suffix = f"M{mode}"
            
            # 获取所有订单
            get_all_orders = getattr(self.main_engine, 'get_all_orders', None)
            if not get_all_orders:
                print("[马丁恢复] 警告: 无法获取订单历史")
                return {'add_count': 0, 'buy_orders': [], 'sell_orders': [], 'total_volume': 0.0}
            
            all_orders = get_all_orders()
            martin_orders = []
            
            # 筛选马丁策略相关订单
            for order in all_orders:
                if (order.vt_symbol == symbol and 
                    order.datetime >= cutoff_time and
                    hasattr(order, 'reference') and
                    mode_suffix in order.reference and
                    'MARTIN' in order.reference.upper()):
                    martin_orders.append(order)
            
            # 分析订单
            buy_orders = [o for o in martin_orders if o.direction == Direction.LONG]
            sell_orders = [o for o in martin_orders if o.direction == Direction.SHORT]
            
            # 计算加仓次数 (只计算成交的买单)
            add_count = 0
            total_buy_volume = 0.0
            
            for order in buy_orders:
                if order.status == Status.ALLTRADED:
                    # 从reference判断是否为加仓单
                    if any(action in order.reference for action in ['ADD_', 'OPEN']):
                        add_count += 1
                        total_buy_volume += float(order.traded)
            
            return {
                'add_count': max(0, add_count - 1),  # 减去首次开仓
                'buy_orders': len(buy_orders),
                'sell_orders': len(sell_orders),
                'total_volume': total_buy_volume,
                'orders': martin_orders
            }
            
        except Exception as e:
            print(f"[马丁恢复] 分析订单历史失败 {symbol} M{mode}: {e}")
            return {'add_count': 0, 'buy_orders': 0, 'sell_orders': 0, 'total_volume': 0.0}
    
    def _get_active_martin_orders(self, symbol: str, mode: int) -> List[str]:
        """获取活跃的马丁策略订单"""
        try:
            mode_suffix = f"M{mode}"
            active_orders = []
            
            # 获取活跃订单
            get_all_active_orders = getattr(self.main_engine, 'get_all_active_orders', None)
            if get_all_active_orders:
                all_active = get_all_active_orders()
                
                for order in all_active:
                    if (order.vt_symbol == symbol and
                        hasattr(order, 'reference') and
                        mode_suffix in order.reference and
                        'MARTIN' in order.reference.upper()):
                        active_orders.append(order.vt_orderid)
            
            return active_orders
            
        except Exception as e:
            print(f"[马丁恢复] 获取活跃订单失败 {symbol} M{mode}: {e}")
            return []
    
    def _validate_recovery_data(self, symbol: str, mode: int, exchange_position: Optional[Dict], 
                              order_analysis: Dict, active_orders: List[str]) -> Dict:
        """验证恢复数据的一致性"""
        has_position = exchange_position and abs(exchange_position['volume']) > 1e-8
        has_orders = len(active_orders) > 0
        has_order_history = order_analysis['total_volume'] > 0
        
        # 一致性检查
        position_order_consistent = True
        if has_position and has_order_history and exchange_position:
            pos_volume = abs(exchange_position['volume'])
            order_volume = order_analysis['total_volume']
            # 允许10%的差异（可能有其他订单）
            if abs(pos_volume - order_volume) / max(pos_volume, order_volume) > 0.1:
                position_order_consistent = False
                print(f"[马丁恢复] ⚠️ 仓位与订单不一致: 仓位={pos_volume}, 订单={order_volume}")
        
        # 方向一致性检查
        direction_consistent = True
        if has_position and exchange_position:
            pos_direction = 1 if exchange_position['volume'] > 0 else 2
            if pos_direction != mode:
                direction_consistent = False
                print(f"[马丁恢复] ⚠️ 仓位方向与策略模式不符: 仓位方向={pos_direction}, 策略模式={mode}")
        
        return {
            'has_position': has_position,
            'has_orders': has_orders,
            'has_order_history': has_order_history,
            'position_order_consistent': position_order_consistent,
            'direction_consistent': direction_consistent,
            'overall_valid': (has_position or has_orders) and direction_consistent
        }
    
    def _determine_recovery_action(self, symbol: str, mode: int, exchange_position: Optional[Dict],
                                 order_analysis: Dict, active_orders: List[str], 
                                 validation: Dict) -> Tuple[str, float]:
        """确定恢复动作和置信度"""
        
        has_position = validation['has_position']
        has_orders = validation['has_orders'] 
        direction_consistent = validation['direction_consistent']
        
        # 计算置信度
        confidence = 0.5  # 基础置信度
        
        if exchange_position and exchange_position.get('source') == 'exchange_query':
            confidence += 0.3  # 交易所验证数据
        
        if validation['position_order_consistent']:
            confidence += 0.2  # 数据一致性
        
        confidence = min(1.0, confidence)
        
        # 决策恢复动作
        if not direction_consistent:
            return "INVALID_DIRECTION", 0.1
        
        if has_position and not has_orders:
            # 有仓位但没有活跃订单，需要重新挂卖单
            return "RESET_SELL", confidence
        
        elif has_position and has_orders:
            # 有仓位也有订单，继续当前策略
            return "CONTINUE", confidence
        
        elif not has_position and has_orders:
            # 无仓位但有订单，可能需要取消订单或等待成交
            return "CANCEL_ORDERS", confidence * 0.8
        
        elif not has_position and not has_orders:
            # 无仓位无订单，准备新周期
            return "NEW_CYCLE", confidence
        
        else:
            return "UNKNOWN", 0.1
    
    def _log_recovery_details(self, state: MartinRecoveryState) -> None:
        """记录恢复详情"""
        mode_text = "做多" if state.mode == 1 else "做空"
        print(f"[马丁恢复] === {state.symbol} {mode_text} 恢复详情 ===")
        print(f"  仓位: {state.total_position:.6f}")
        print(f"  成本价: {state.avg_cost_price:.6f}")
        print(f"  加仓次数: {state.add_count}")
        print(f"  活跃订单: {len(state.active_orders)}")
        print(f"  恢复动作: {state.recovery_action}")
        print(f"  置信度: {state.confidence:.2f}")
        print(f"  交易所验证: {'是' if state.exchange_verified else '否'}")


# =============================================================================
# 持久化管理器 - 优化版本
# =============================================================================

class PersistenceManager:
    """
    优化的持久化管理器
    
    支持：
    1. 智能频率控制
    2. 关键状态变化立即保存
    3. 基于交易所数据的恢复
    4. 数据完整性验证
    """
    
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.data_dir = Path(f"./data/{account_id}")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件路径
        self.state_file = self.data_dir / "executor_state.json"
        self.martin_states_file = self.data_dir / "martin_states.json"
        self.order_mapping_file = self.data_dir / "order_mapping.json"
        self.coordination_history_file = self.data_dir / "coordination_history.json"
        
        # 持久化控制
        self.last_critical_save = datetime.now()
        self.critical_save_interval = 60  # 关键变化最小间隔60秒
        self.dirty_flags = {
            'executor': False,
            'martin': False,
            'orders': False
        }
    
    def mark_critical_change(self, category: str) -> None:
        """标记关键变化"""
        self.dirty_flags[category] = True
        
        # 如果距离上次关键保存超过间隔，立即保存
        if (datetime.now() - self.last_critical_save).seconds >= self.critical_save_interval:
            self._save_critical_data()
    
    def _save_critical_data(self) -> None:
        """保存关键数据"""
        try:
            if self.dirty_flags['martin']:
                # 这里会在AccountExecutor中调用
                pass  
            
            self.last_critical_save = datetime.now()
            # 重置脏标记
            for key in self.dirty_flags:
                self.dirty_flags[key] = False
                
        except Exception as e:
            print(f"[持久化] 保存关键数据失败: {e}")
    
    def save_martin_states(self, martin_states: Dict[str, MartinState]) -> None:
        """保存马丁策略状态"""
        try:
            states_data = {}
            for strategy_key, state in martin_states.items():
                states_data[strategy_key] = {
                    'symbol': state.symbol,
                    'mode': state.mode,
                    'avg_price': state.avg_price,
                    'position_size': state.position_size,
                    'add_count': state.add_count,
                    'total_margin_used': state.total_margin_used,
                    'active_orders': state.active_orders,
                    'execution_mode': state.execution_mode.value,
                    'last_update': state.last_update.isoformat()
                }
            
            save_data = {
                'account_id': self.account_id,
                'timestamp': datetime.now().isoformat(),
                'martin_states': states_data,
                'recovery_note': 'Use exchange data for primary recovery'
            }
            
            with open(self.martin_states_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"保存马丁策略状态失败: {e}")
    
    def load_martin_states(self) -> Dict[str, dict]:
        """加载马丁策略状态 (仅作为备份参考)"""
        try:
            if not self.martin_states_file.exists():
                return {}
                
            with open(self.martin_states_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print("[持久化] 注意: 本地状态仅作为参考，实际恢复将基于交易所数据")
                return data.get('martin_states', {})
                
        except Exception as e:
            print(f"加载马丁策略状态失败: {e}")
            return {}
    
    def save_executor_state(self, executor_state: dict) -> None:
        """保存执行器状态"""
        try:
            state_data = {
                'account_id': self.account_id,
                'timestamp': datetime.now().isoformat(),
                'version': '2.0',  # 新版本标记
                'state': executor_state
            }
            
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False, default=str)
                
        except Exception as e:
            print(f"保存执行器状态失败: {e}")
    
    def load_executor_state(self) -> Optional[dict]:
        """加载执行器状态"""
        try:
            if not self.state_file.exists():
                return None
                
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('state')
                
        except Exception as e:
            print(f"加载执行器状态失败: {e}")
            return None
    
    def save_order_mapping(self, order_mapping: dict) -> None:
        """保存订单映射关系"""
        try:
            save_data = {
                'account_id': self.account_id,
                'timestamp': datetime.now().isoformat(),
                'order_mapping': order_mapping
            }
            
            with open(self.order_mapping_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
                
        except Exception as e:
            print(f"保存订单映射失败: {e}")
    
    def load_order_mapping(self) -> dict:
        """加载订单映射关系"""
        try:
            if not self.order_mapping_file.exists():
                return {}
                
            with open(self.order_mapping_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('order_mapping', {})
                
        except Exception as e:
            print(f"加载订单映射失败: {e}")
            return {}


# =============================================================================
# 主要账户执行器类 - 支持多模式马丁策略
# =============================================================================

class AccountExecutor:
    """
    账户执行器 - 核心执行器类
    
    功能：
    1. 基于MainEngine的事件驱动交易执行
    2. 多交易对、多模式马丁策略管理
    3. 趋势信号接收和策略协调
    4. 订单管理和状态持久化
    5. 风险控制和监控
    
    架构特点：
    - 使用MainEngine直接进行订单管理，无策略框架依赖
    - 事件驱动响应，支持on_order和on_trade回调
    - 完整的状态持久化和恢复机制
    - 支持趋势策略信号接入
    - 支持同一交易对的多种马丁策略模式
    """
    
    def __init__(self, account_id: str, main_engine: MainEngine, event_engine: EventEngine):
        """
        初始化账户执行器
        
        参数：
        - account_id: 账户标识符
        - main_engine: HowTrader主引擎
        - event_engine: 事件引擎
        """
        self.account_id = account_id
        self.main_engine = main_engine
        self.event_engine = event_engine
        
        # 组件初始化
        self.order_manager = OrderManager(account_id)
        #self.strategy_coordinator = StrategyCoordinator()   未来策略优化后可能启动
        self.persistence_manager = PersistenceManager(account_id)
        
        # 马丁策略恢复器
        self.martin_recovery = MartinStateRecovery(main_engine, account_id)
        
        # 马丁策略管理器集合 {symbol_mode: MartinManager}
        # 例如: "BTC-USDT-SWAP.OKX_M1", "BTC-USDT-SWAP.OKX_M2"
        # M1=做多马丁, M2=做空马丁
        self.martin_managers: Dict[str, MartinManager] = {}
        
        # 趋势信号缓存 {symbol: TrendSignal} - 暂时注释
        # self.trend_signals: Dict[str, TrendSignal] = {}
        
        # 执行器状态
        self.active = False
        self.supported_symbols: Set[str] = set()  # 支持的交易对
        self.last_heartbeat = datetime.now()
        
        # 定时器
        self.timer_thread: Optional[threading.Thread] = None
        self.timer_interval = 30  # 30秒定时任务
        
        # 统计信息
        self.stats = {
            'total_orders': 0,
            'total_trades': 0,
            'start_time': datetime.now(),
            'last_activity': datetime.now()
        }
        
        print(f"账户执行器 {account_id} 初始化完成")
    
    def _recover_state(self) -> None:
        """
        智能状态恢复 - 基于交易所数据的混合方案
        
        恢复策略：
        1. 优先使用交易所权威数据（仓位、价格）
        2. 通过本地配置恢复策略参数
        3. 通过订单历史分析策略状态
        4. 智能决策恢复动作
        """
        try:
            print(f"[{self.account_id}] 开始智能状态恢复...")
            
            # 1. 恢复执行器基础状态
            executor_state = self.persistence_manager.load_executor_state()
            if executor_state:
                self.supported_symbols = set(executor_state.get('supported_symbols', []))
                if 'stats' in executor_state:
                    self.stats.update(executor_state['stats'])
                print(f"[{self.account_id}] 执行器基础状态已恢复")
            
            # 2. 恢复订单映射关系
            order_mapping = self.persistence_manager.load_order_mapping()
            if order_mapping:
                self.order_manager.order_mapping = order_mapping
                print(f"[{self.account_id}] 订单映射已恢复: {len(order_mapping)}个订单")
            
            # 3. 智能恢复马丁策略状态
            if self.supported_symbols:
                symbols_list = list(self.supported_symbols)
                recovery_results = self.martin_recovery.recover_martin_strategies(
                    symbols=symbols_list,
                    modes=None,  # 尝试所有模式
                    lookback_hours=48  # 回看48小时
                )
                
                # 4. 应用恢复结果
                self._apply_recovery_results(recovery_results)
            
            print(f"[{self.account_id}] 智能状态恢复完成")
            
        except Exception as e:
            print(f"[{self.account_id}] 状态恢复失败: {e}")
            print(f"[{self.account_id}] 将以全新状态启动")
    
    def _apply_recovery_results(self, recovery_results: Dict[str, MartinRecoveryState]) -> None:
        """应用恢复结果到马丁管理器"""
        for strategy_key, recovery_state in recovery_results.items():
            
            # 检查是否已有对应的马丁管理器
            if strategy_key in self.martin_managers:
                martin_manager = self.martin_managers[strategy_key]
                
                # 更新马丁状态
                martin_manager.state.avg_price = recovery_state.avg_cost_price
                martin_manager.state.position_size = recovery_state.total_position
                martin_manager.state.add_count = recovery_state.add_count
                martin_manager.state.active_orders = recovery_state.active_orders.copy()
                martin_manager.state.last_update = datetime.now()
                
                # 根据恢复动作设置执行模式
                if recovery_state.recovery_action == "RESET_SELL":
                    martin_manager.state.execution_mode = ExecutionMode.NORMAL
                    # 需要重新挂卖单
                    self._place_recovery_sell_order(martin_manager, recovery_state)
                    
                elif recovery_state.recovery_action == "CONTINUE":
                    martin_manager.state.execution_mode = ExecutionMode.NORMAL
                    
                elif recovery_state.recovery_action == "CANCEL_ORDERS":
                    martin_manager.state.execution_mode = ExecutionMode.POSITION_ONLY
                    # 取消无用订单
                    self._cancel_recovery_orders(martin_manager, recovery_state)
                    
                elif recovery_state.recovery_action == "NEW_CYCLE":
                    martin_manager.state.execution_mode = ExecutionMode.NORMAL
                    # 重置状态准备新周期
                    martin_manager._reset_martin_state()
                
                mode_text = "做多" if recovery_state.mode == 1 else "做空"
                print(f"[{self.account_id}] ✅ {recovery_state.symbol} {mode_text} 策略已恢复")
                print(f"  仓位: {recovery_state.total_position:.6f}")
                print(f"  成本: {recovery_state.avg_cost_price:.6f}")
                print(f"  加仓次数: {recovery_state.add_count}")
                print(f"  恢复动作: {recovery_state.recovery_action}")
                print(f"  置信度: {recovery_state.confidence:.2f}")
            else:
                print(f"[{self.account_id}] ⚠️ 未找到对应的马丁管理器: {strategy_key}")
    
    def _place_recovery_sell_order(self, martin_manager: MartinManager, recovery_state: MartinRecoveryState) -> None:
        """放置恢复卖单"""
        try:
            if recovery_state.total_position <= 0:
                return
            
            # 获取当前价格
            tick = getattr(self.main_engine, 'get_tick', lambda x: None)(martin_manager.symbol)
            if not tick or not tick.last_price:
                print(f"[{self.account_id}] 无法获取 {martin_manager.symbol} 当前价格，暂缓挂卖单")
                return
            
            current_price = float(tick.last_price)
            
            # 计算止盈价格
            if martin_manager.mode == 1:  # 做多
                profit_price = recovery_state.avg_cost_price * (1 + martin_manager.profit_target)
            else:  # 做空
                profit_price = recovery_state.avg_cost_price * (1 - martin_manager.profit_target)
            
            # 创建止盈订单
            order_req = martin_manager._calculate_profit_order(current_price)
            if order_req:
                vt_orderid = self._send_order(order_req, martin_manager.symbol)
                if vt_orderid:
                    martin_manager.state.active_orders.append(vt_orderid)
                    print(f"[{self.account_id}] 恢复卖单已挂出: {vt_orderid}")
            
        except Exception as e:
            print(f"[{self.account_id}] 放置恢复卖单失败: {e}")
    
    def _cancel_recovery_orders(self, martin_manager: MartinManager, recovery_state: MartinRecoveryState) -> None:
        """取消恢复过程中的无用订单"""
        try:
            for order_id in recovery_state.active_orders:
                try:
                    # 使用getattr安全调用
                    get_order = getattr(self.main_engine, 'get_order', None)
                    if get_order:
                        order = get_order(order_id)
                        if order and order.is_active():
                            cancel_req = order.create_cancel_request()
                            gateway_name = getattr(order, 'gateway_name', 'OKX')
                            self.main_engine.cancel_order(cancel_req, gateway_name)
                            print(f"[{self.account_id}] 已取消无用订单: {order_id}")
                except Exception as e:
                    print(f"[{self.account_id}] 取消订单失败 {order_id}: {e}")
                    
        except Exception as e:
            print(f"[{self.account_id}] 取消恢复订单过程失败: {e}")
    
    def start(self) -> None:
        """启动账户执行器"""
        if self.active:
            print(f"账户执行器 {self.account_id} 已经在运行中")
            return
        
        print(f"启动账户执行器 {self.account_id}")
        
        # 注册事件监听
        self._register_events()
        
        # 智能恢复状态
        self._recover_state()
        
        # 启动定时器
        self._start_timer()
        
        self.active = True
        self.last_heartbeat = datetime.now()
        
        print(f"账户执行器 {self.account_id} 启动成功")
    
    def stop(self) -> None:
        """停止账户执行器"""
        if not self.active:
            return
        
        print(f"停止账户执行器 {self.account_id}")
        
        # 保存状态
        self._save_state()
        
        # 停止定时器
        self._stop_timer()
        
        # 注销事件监听
        self._unregister_events()
        
        self.active = False
        
        print(f"账户执行器 {self.account_id} 已停止")
    
    def add_martin_strategy(self, symbol: str, mode: int, config: dict) -> bool:
        """
        添加马丁策略
        
        参数：
        - symbol: 交易对符号 (如: 'BTC-USDT-SWAP.OKX')
        - mode: 马丁模式 (1=做多, 2=做空)
        - config: 马丁策略配置
        
        返回：是否添加成功
        """
        # 创建唯一的策略键值：symbol + mode
        # M1=做多马丁, M2=做空马丁
        mode_suffix = "M1" if mode == 1 else "M2"
        strategy_key = f"{symbol}_{mode_suffix}"
        
        if strategy_key in self.martin_managers:
            mode_text = "做多" if mode == 1 else "做空"
            print(f"交易对 {symbol} 的 {mode_text} 马丁策略已存在")
            return False
        
        try:
            # 创建马丁管理器
            martin_manager = MartinManager(
                account_id=self.account_id,
                symbol=symbol,
                mode=mode,
                config=config,
                order_manager=self.order_manager,
                main_engine=self.main_engine
            )
            
            self.martin_managers[strategy_key] = martin_manager
            self.supported_symbols.add(symbol)
            
            mode_text = "做多" if mode == 1 else "做空"
            print(f"成功添加马丁策略: {symbol} {mode_text} 模式")
            return True
            
        except Exception as e:
            mode_text = "做多" if mode == 1 else "做空"
            print(f"添加马丁策略失败 {symbol} {mode_text}: {e}")
            return False
    
    def remove_martin_strategy(self, symbol: str, mode: int) -> bool:
        """移除指定模式的马丁策略 - 使用安全方法"""
        mode_suffix = "M1" if mode == 1 else "M2"
        strategy_key = f"{symbol}_{mode_suffix}"
        
        mode_text = "做多" if mode == 1 else "做空"
        if strategy_key not in self.martin_managers:
            print(f"交易对 {symbol} 的 {mode_text} 马丁策略不存在")
            return False
        
        try:
            # 取消该策略的所有活跃订单
            martin_manager = self.martin_managers[strategy_key]
            for order_id in martin_manager.state.active_orders:
                try:
                    # 使用安全方法获取订单
                    get_order = getattr(self.main_engine, 'get_order', None)
                    if get_order:
                        order = get_order(order_id)
                        if order and order.is_active():
                            cancel_req = order.create_cancel_request()
                            gateway_name = getattr(order, 'gateway_name', 'OKX')
                            self.main_engine.cancel_order(cancel_req, gateway_name)
                except Exception as e:
                    print(f"取消订单失败 {order_id}: {e}")
            
            # 移除管理器
            del self.martin_managers[strategy_key]
            
            # 检查symbol是否还在使用
            symbol_still_used = any(key.startswith(f"{symbol}_") for key in self.martin_managers.keys())
            if not symbol_still_used:
                self.supported_symbols.discard(symbol)
            
            print(f"成功移除马丁策略: {symbol} {mode_text} 模式")
            return True
            
        except Exception as e:
            print(f"移除马丁策略失败 {symbol} {mode_text}: {e}")
            return False
    
    def batch_send_orders(self, order_requests: List[OrderRequest]) -> List[str]:
        """批量发送马丁策略订单"""
        vt_orderids = []
        for order_req in order_requests:
            vt_orderid = self._send_order(order_req, order_req.symbol)
            if vt_orderid:
                vt_orderids.append(vt_orderid)  
        return vt_orderids
    
    def _start_timer(self) -> None:
        """启动定时任务"""
        if self.timer_thread and self.timer_thread.is_alive():
            return
        
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()
        print(f"[{self.account_id}] 定时器已启动")
    
    def _stop_timer(self) -> None:
        """停止定时任务"""
        # 定时器线程会在active=False时自动退出
        if self.timer_thread and self.timer_thread.is_alive():
            self.timer_thread.join(timeout=5)
        print(f"[{self.account_id}] 定时器已停止")
    
    def _timer_loop(self) -> None:
        """定时任务循环"""
        while self.active:
            try:
                # 更新心跳
                self.last_heartbeat = datetime.now()
                
                # 定期保存状态
                self._save_state()
                
                # 健康检查
                self._health_check()
                
                # 等待下一次执行
                time.sleep(self.timer_interval)
                
            except Exception as e:
                print(f"[{self.account_id}] 定时任务异常: {e}")
                time.sleep(5)  # 异常时短暂等待
    
    def _health_check(self) -> None:
        """健康检查"""
        try:
            # 检查各个马丁管理器状态
            for strategy_key, martin_manager in self.martin_managers.items():
                state = martin_manager.get_state()
                
                # 检查状态是否正常
                if state.execution_mode == ExecutionMode.EMERGENCY_EXIT:
                    print(f"[{self.account_id}] 警告: {strategy_key} 马丁策略处于紧急退出模式")
                
                # 检查最后更新时间
                if (datetime.now() - state.last_update).seconds > 300:  # 5分钟无更新
                    print(f"[{self.account_id}] 警告: {strategy_key} 马丁策略长时间无更新")
            
            # 打印运行状态
            uptime = datetime.now() - self.stats['start_time']
            print(f"[{self.account_id}] 运行状态: 正常 | 运行时间: {uptime} | "
                  f"总订单: {self.stats['total_orders']} | 总成交: {self.stats['total_trades']}")
                  
        except Exception as e:
            print(f"[{self.account_id}] 健康检查失败: {e}")
    
    def _save_state(self) -> None:
        """保存状态 - 优化版本"""
        try:
            # 保存执行器状态
            executor_state = {
                'active': self.active,
                'supported_symbols': list(self.supported_symbols),
                'stats': self.stats,
                'last_heartbeat': self.last_heartbeat.isoformat()
            }
            self.persistence_manager.save_executor_state(executor_state)
            
            # 保存马丁策略状态
            martin_states = {}
            for strategy_key, martin_manager in self.martin_managers.items():
                martin_states[strategy_key] = martin_manager.get_state()
            
            if martin_states:
                self.persistence_manager.save_martin_states(martin_states)
            
            # 保存订单映射
            self.persistence_manager.save_order_mapping(self.order_manager.order_mapping)
            
            # 标记已保存
            self.persistence_manager.mark_critical_change('martin')
            
        except Exception as e:
            print(f"[{self.account_id}] 保存状态失败: {e}")
    
    def _register_events(self) -> None:
        """注册事件监听"""
        # 监听交易相关事件
        self.event_engine.register(EVENT_ORDER, self.on_order)
        self.event_engine.register(EVENT_TRADE, self.on_trade)
        self.event_engine.register(EVENT_POSITION, self.on_position)
        
        # 监听趋势信号事件
        #self.event_engine.register(EVENT_TREND_SIGNAL, self.on_trend_signal)
        
        print(f"[{self.account_id}] 事件监听已注册")
    
    def _unregister_events(self) -> None:
        """注销事件监听"""
        try:
            self.event_engine.unregister(EVENT_ORDER, self.on_order)
            self.event_engine.unregister(EVENT_TRADE, self.on_trade)
            self.event_engine.unregister(EVENT_POSITION, self.on_position)
            #self.event_engine.unregister(EVENT_TREND_SIGNAL, self.on_trend_signal)
            print(f"[{self.account_id}] 事件监听已注销")
        except Exception as e:
            print(f"[{self.account_id}] 注销事件监听失败: {e}")
    
    def on_order(self, event: Event) -> None:
        """
        订单事件处理器
        
        核心功能：
        1. 识别订单归属
        2. 更新订单状态
        3. 触发马丁策略更新
        """
        order: OrderData = event.data
        print(f"[{self.account_id}] 收到订单事件: {order.vt_symbol} {order.vt_orderid} {order.status}")
        # 只处理本账户的订单
        if not self.order_manager.is_my_order(order):
            return
        
        try:
            # 更新统计信息
            self.stats['total_orders'] += 1
            self.stats['last_activity'] = datetime.now()
            
            # 分类订单
            category = self.order_manager.classify_order(order)
            
            # 更新订单管理器
            # 这句代码的作用是：当订单已经不是活跃状态（比如已成交、已撤销等），并且该订单ID在订单管理器的记录中时，
            # 就把这个订单从订单管理器的映射（order_mapping）里移除，避免无效订单一直占用内存。
            if not order.is_active() and order.vt_orderid in self.order_manager.order_mapping:
                self.order_manager.remove_order(order.vt_orderid)
            
            # 如果是马丁策略订单，通知对应的马丁管理器
            if category == OrderCategory.MARTIN and order.vt_symbol in self.supported_symbols:
                # 查找对应的马丁管理器（可能有多个模式）
                for strategy_key, martin_manager in self.martin_managers.items():
                    if martin_manager.symbol == order.vt_symbol:
                        martin_manager.on_order_update(order)
                        # 检查是否需要生成新的订单
                        self._check_generate_orders(martin_manager.symbol, martin_manager.mode)
            
            # 打印订单更新日志
            status_text = {
                Status.SUBMITTING: "提交中",
                Status.NOTTRADED: "未成交", 
                Status.PARTTRADED: "部分成交",
                Status.ALLTRADED: "全部成交",
                Status.CANCELLED: "已撤销",
                Status.REJECTED: "已拒绝"
            }.get(order.status, str(order.status))
            
            print(f"[{self.account_id}] 订单更新: {order.vt_symbol} {category.value} {status_text} "
                  f"价格={order.price} 数量={order.volume}")
                  
        except Exception as e:
            print(f"[{self.account_id}] 处理订单事件失败: {e}")
    
    def on_trade(self, event: Event) -> None:
        """
        成交事件处理器
        
        核心功能：
        1. 更新仓位计算
        2. 触发马丁策略调整
        3. 记录成交历史
        """
        trade: TradeData = event.data
        
        # 只处理支持的交易对
        if trade.vt_symbol not in self.supported_symbols:
            return
        
        try:
            # 更新统计信息
            self.stats['total_trades'] += 1
            self.stats['last_activity'] = datetime.now()
            
            # 通知所有相关的马丁管理器
            for strategy_key, martin_manager in self.martin_managers.items():
                if martin_manager.symbol == trade.vt_symbol:
                    martin_manager.on_trade_update(trade)
                    # 检查是否需要生成新的订单
                    self._check_generate_orders(martin_manager.symbol, martin_manager.mode)
            
            # 打印成交日志
            direction_text = "买入" if trade.direction == Direction.LONG else "卖出"
            print(f"[{self.account_id}] 成交通知: {trade.vt_symbol} {direction_text} "
                  f"价格={trade.price} 数量={trade.volume}")
                  
        except Exception as e:
            print(f"[{self.account_id}] 处理成交事件失败: {e}")
    
    def on_position(self, event: Event) -> None:
        """
        仓位事件处理器
        
        功能：监控仓位变化，用于状态验证
        """
        position: PositionData = event.data
        
        if position.vt_symbol in self.supported_symbols:
            print(f"[{self.account_id}] 仓位更新: {position.vt_symbol} 数量={position.volume}")
    '''
    def on_trend_signal(self, event: Event) -> None:
        """
        趋势信号事件处理器
        
        核心功能：
        1. 接收趋势策略引擎信号
        2. 缓存信号数据
        3. 触发策略协调
        """
        signal_data = event.data
        
        try:
            # 解析趋势信号
            trend_signal = TrendSignal(
                symbol=signal_data['symbol'],
                timeframes=signal_data.get('timeframes', {}),
                overall_direction=signal_data['overall']['direction'],
                overall_strength=signal_data['overall']['strength'],
                confidence=signal_data['overall']['confidence'],
                timestamp=datetime.fromisoformat(signal_data['timestamp']),
                source=signal_data['source']
            )
            
            # 只处理支持的交易对
            if trend_signal.symbol not in self.supported_symbols:
                return
            
            # 缓存信号
            self.trend_signals[trend_signal.symbol] = trend_signal
            
            # 对所有相关的马丁策略进行协调
            for strategy_key, martin_manager in self.martin_managers.items():
                if martin_manager.symbol == trend_signal.symbol:
                    # 策略协调
                    self.strategy_coordinator.coordinate_strategies(
                        symbol=trend_signal.symbol,
                        trend_signal=trend_signal,
                        martin_manager=martin_manager
                    )
                    
                    # 检查是否需要生成新的订单
                    self._check_generate_orders(trend_signal.symbol, martin_manager.mode)
            
            print(f"[{self.account_id}] 收到趋势信号: {trend_signal.symbol} "
                  f"方向={trend_signal.overall_direction} 强度={trend_signal.overall_strength:.2f}")
                  
        except Exception as e:
            print(f"[{self.account_id}] 处理趋势信号失败: {e}")
    '''
    def _check_generate_orders(self, symbol: str, mode: int) -> None:
        """
        检查是否需要生成新订单
        
        功能：
        1. 根据马丁策略状态,决定是否发送新订单
        2. 使用安全的价格获取方法
        """
        mode_suffix = "M1" if mode == 1 else "M2"
        strategy_key = f"{symbol}_{mode_suffix}"
        
        if strategy_key not in self.martin_managers:
            return
        
        try:
            martin_manager = self.martin_managers[strategy_key]
            
            # 获取当前价格 - 使用安全方法
            tick = getattr(self.main_engine, 'get_tick', lambda x: None)(symbol)
            if not tick or not tick.last_price:
                return
            
            current_price = tick.last_price
            
            # 获取趋势信号 - 暂时注释掉
            # trend_signal = self.trend_signals.get(symbol)
            trend_signal = None
            
            # 计算下一步动作
            order_req = martin_manager.calculate_next_action(current_price, trend_signal)
            
            if order_req:
                # 发送订单
                self._send_order(order_req, symbol)
                
        except Exception as e:
            mode_text = "做多" if mode == 1 else "做空"
            print(f"[{self.account_id}] 检查生成订单失败 {symbol} {mode_text}: {e}")
    
    def _send_order(self, order_req: OrderRequest, symbol: str) -> Optional[str]:
        """
        发送订单 - 使用安全的合约获取方法
        
        返回：订单ID或None
        """
        try:
            # 获取合约信息 - 使用安全方法
            contract = getattr(self.main_engine, 'get_contract', lambda x: None)(symbol)
            if not contract:
                print(f"无法获取合约信息: {symbol}")
                return None
            
            # 向交易所发送订单
            vt_orderid = self.main_engine.send_order(order_req, contract.gateway_name)
            
            if vt_orderid:
                # 注册订单
                category = OrderCategory.MARTIN  # 暂时只支持马丁订单
                action = order_req.reference.split('_')[-2] if '_' in order_req.reference else "UNKNOWN"
                
                self.order_manager.register_order(
                    order_id=vt_orderid,
                    symbol=symbol,
                    category=category,
                    action=action,
                    reference=order_req.reference  #订单标识reference="MARTIN_ACC001_BTCUSDT_OPEN_001"
                )
                
                print(f"[{self.account_id}] 发送订单成功: {symbol} {order_req.reference} ID={vt_orderid}")
                return vt_orderid
            else:
                print(f"[{self.account_id}] 发送订单失败: {symbol} {order_req.reference}")
                return None
                
        except Exception as e:
            print(f"[{self.account_id}] 发送订单异常: {e}")
            return None
    
    def _cancel_symbol_orders(self, symbol: str) -> None:
        """取消指定交易对的所有活跃订单 - 使用安全方法"""
        try:
            active_orders = self.order_manager.get_active_orders_by_symbol(symbol)
            
            for order_id in active_orders:
                try:
                    # 获取订单信息 - 使用安全方法
                    get_order = getattr(self.main_engine, 'get_order', None)
                    if get_order:
                        order = get_order(order_id)
                        if order and order.is_active():
                            # 创建撤单请求
                            cancel_req = order.create_cancel_request()
                            gateway_name = getattr(order, 'gateway_name', 'OKX')
                            self.main_engine.cancel_order(cancel_req, gateway_name)
                            print(f"[{self.account_id}] 撤销订单: {order_id}")
                        
                except Exception as e:
                    print(f"[{self.account_id}] 撤销订单失败 {order_id}: {e}")
                    
        except Exception as e:
            print(f"[{self.account_id}] 取消交易对订单失败 {symbol}: {e}")
    
    def get_status(self) -> dict:
        """
        获取账户执行器状态 - 更新版本
        
        返回完整的状态信息
        """
        try:
            martin_status = {}
            for strategy_key, martin_manager in self.martin_managers.items():
                state = martin_manager.get_state()
                martin_status[strategy_key] = {
                    'symbol': state.symbol,
                    'mode': '做多' if state.mode == 1 else '做空',
                    'avg_price': state.avg_price,
                    'position_size': state.position_size,
                    'add_count': state.add_count,
                    'total_margin_used': state.total_margin_used,
                    'execution_mode': state.execution_mode.value,
                    'active_orders_count': len(state.active_orders),
                    'last_update': state.last_update.isoformat()
                }
            
            # 暂时注释趋势信号状态
            trend_signals_status = {}
            # for symbol, signal in self.trend_signals.items():
            #     trend_signals_status[symbol] = {
            #         'direction': signal.overall_direction,
            #         'strength': signal.overall_strength,
            #         'confidence': signal.confidence,
            #         'timestamp': signal.timestamp.isoformat(),
            #         'source': signal.source
            #     }
            
            return {
                'account_id': self.account_id,
                'active': self.active,
                'supported_symbols': list(self.supported_symbols),
                'stats': self.stats,
                'last_heartbeat': self.last_heartbeat.isoformat(),
                'martin_strategies': martin_status,
                'trend_signals': trend_signals_status,
                'recovery_enabled': True,
                'recovery_method': 'exchange_data_primary'
            }
            
        except Exception as e:
            print(f"[{self.account_id}] 获取状态失败: {e}")
            return {'error': str(e)}
    
    def emergency_stop(self) -> None:
        """紧急停止"""
        print(f"[{self.account_id}] 执行紧急停止...")
        
        try:
            # 设置所有马丁策略为紧急退出模式
            for strategy_key, martin_manager in self.martin_managers.items():
                martin_manager.set_execution_mode(ExecutionMode.EMERGENCY_EXIT)
            
            # 取消所有活跃订单
            for symbol in self.supported_symbols:
                self._cancel_symbol_orders(symbol)
            
            print(f"[{self.account_id}] 紧急停止执行完成")
            
        except Exception as e:
            print(f"[{self.account_id}] 紧急停止失败: {e}")


# =============================================================================
# 使用示例和测试代码 - 展示新的恢复系统
# =============================================================================

if __name__ == "__main__":
    """
    AccountExecutor使用示例 - 重点展示智能恢复功能
    """
    # 模拟HowTrader引擎初始化
    from howtrader.event import EventEngine
    from howtrader.trader.engine import MainEngine
    
    # 创建引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    
    # 创建账户执行器
    account_id = "TEST_ACCOUNT_001"
    executor = AccountExecutor(account_id, main_engine, event_engine)
    
    # 马丁策略配置
    martin_config = {
        'lever': 10,                  # 10倍杠杆
        'first_margin': 50.0,         # 首次50USDT
        'first_margin_add': 50.0,     # 加仓50USDT
        'adding_number': 10,          # 最多10次加仓
        'amount_multiplier': 1.2,     # 每次加仓增加20%
        'profit_target': 0.01,        # 1%止盈
        'opp_ratio': 0.025,           # 2.5%时加仓
        'max_total_margin': 1000.0    # 最大1000USDT保证金
    }
    
    try:
        # 启动执行器 (包含智能恢复)
        print("=" * 60)
        print("启动账户执行器 - 新版智能恢复系统")
        print("=" * 60)
        executor.start()
        
        # 添加交易对策略
        symbol = "BTC-USDT-SWAP.OKX"
        
        # 做多模式 (M1)
        if executor.add_martin_strategy(symbol, 1, martin_config):
            print(f"✅ 成功添加马丁策略: {symbol} 做多模式 (M1)")
        
        # 做空模式 (M2)
        if executor.add_martin_strategy(symbol, 2, martin_config):
            print(f"✅ 成功添加马丁策略: {symbol} 做空模式 (M2)")
        
        print("\n" + "=" * 60)
        print("智能恢复系统特性展示")
        print("=" * 60)
        
        # 展示恢复功能
        print("📊 恢复系统特性:")
        print("  1. ✅ 优先使用交易所权威数据 (仓位、平均价)")
        print("  2. ✅ 通过订单历史分析补充策略细节 (加仓次数)")
        print("  3. ✅ 交叉验证确保数据一致性")
        print("  4. ✅ 智能决策恢复动作 (RESET_SELL/CONTINUE/NEW_CYCLE)")
        print("  5. ✅ 支持多模式马丁策略同时恢复")
        print("  6. ✅ 容错处理，即使部分数据缺失也能恢复")
        
        # 获取状态
        print("\n📋 账户执行器状态:")
        status = executor.get_status()
        for key, value in status.items():
            if key == 'martin_strategies':
                print(f"  {key}:")
                for strategy_key, strategy_status in value.items():
                    print(f"    {strategy_key}: {strategy_status}")
            else:
                print(f"  {key}: {value}")
        
        print(f"\n🔄 恢复方法: {status.get('recovery_method', 'unknown')}")
        print(f"📡 恢复功能: {'启用' if status.get('recovery_enabled') else '禁用'}")
        
        # 模拟测试恢复功能
        print("\n" + "=" * 60)
        print("模拟恢复功能测试")
        print("=" * 60)
        
        # 测试马丁恢复器
        recovery_results = executor.martin_recovery.recover_martin_strategies(
            symbols=[symbol],
            modes=[1, 2],  # 测试两种模式
            lookback_hours=24
        )
        
        print(f"🔍 恢复分析结果: 发现 {len(recovery_results)} 个策略需要处理")
        for strategy_key, recovery_state in recovery_results.items():
            print(f"  {strategy_key}:")
            print(f"    恢复动作: {recovery_state.recovery_action}")
            print(f"    置信度: {recovery_state.confidence:.2f}")
            print(f"    交易所验证: {'是' if recovery_state.exchange_verified else '否'}")
        
    except KeyboardInterrupt:
        print("\n收到停止信号...")
        
    finally:
        # 停止执行器
        executor.stop()
        print("\n🛑 账户执行器已停止")
        print("\n" + "=" * 60)
        print("重构完成总结")
        print("=" * 60)
        print("🎯 重构后的马丁策略恢复系统:")
        print("  1. 基于交易所数据的混合恢复方案")
        print("  2. 智能状态分析和一致性验证")
        print("  3. 自动恢复动作决策")
        print("  4. 完整的错误处理和日志记录")
        print("  5. 支持多交易对和多模式马丁策略")
        print("\n🚀 系统已准备好处理实际交易中断恢复场景!")
        print("=" * 60)