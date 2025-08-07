"""
HowTrader 分布式微服务架构
解决多账户资源浪费问题的完整方案

架构设计：
1. 行情数据服务 (1个容器) - 统一获取行情数据
2. 策略计算引擎 (1个容器) - 统一计算技术指标和生成信号  
3. 账户管理服务 (N个容器) - 每个账户独立管理订单和仓位
4. 监控管理服务 (1个容器) - 统一监控和报警
"""

import json
import threading
# 注意：实际使用时需要安装 redis: pip install redis
# import redis
from time import sleep
from datetime import datetime
from logging import INFO
from typing import Dict, List, Any
from decimal import Decimal

from howtrader.event import EventEngine, Event
from howtrader.trader.setting import SETTINGS  
from howtrader.trader.engine import MainEngine
from howtrader.trader.object import TickData, BarData, OrderData, TradeData, PositionData
from howtrader.trader.constant import Direction, Offset
from howtrader.trader.utility import ArrayManager

# 导入网关
from howtrader.gateway.okx import OkxGateway
from howtrader.gateway.binance import BinanceUsdtGateway

# 导入策略
from howtrader.app.cta_strategy import CtaStrategyApp, CtaTemplate

# ===============================
# 🌐 消息总线 - Redis消息中介
# ===============================

class MessageBus:
    """
    消息总线 - 演示版本（生产环境请使用Redis）
    实际部署时需要：pip install redis
    """
    
    def __init__(self, host='localhost', port=6379, db=0):
        print(f"💡 演示模式：实际部署时连接 Redis {host}:{port}")
        # 演示版本：使用内存字典模拟
        self.subscribers = {}
        self.messages = []
        
    def publish(self, channel: str, message: dict):
        """发布消息（演示版本）"""
        print(f"📤 发布消息到 {channel}: {json.dumps(message, indent=2)}")
        # 实际实现: self.redis_client.publish(channel, json.dumps(message))
        
    def subscribe(self, channels: List[str], callback):
        """订阅消息（演示版本）"""
        for channel in channels:
            print(f"📡 订阅频道: {channel}")
            self.subscribers[channel] = callback
        # 实际实现需要 Redis pubsub

# ===============================
# 📡 服务1：行情数据服务
# ===============================

class MarketDataService:
    """
    行情数据服务
    - 统一获取所有交易所的行情数据
    - 通过消息总线分发给其他服务
    - 避免重复连接和数据获取
    """
    
    def __init__(self):
        print("🚀 启动行情数据服务...")
        
        # 初始化HowTrader核心组件
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        
        # 添加交易所网关
        self.main_engine.add_gateway(OkxGateway)
        self.main_engine.add_gateway(BinanceUsdtGateway)
        
        # 初始化消息总线
        self.message_bus = MessageBus()
        
        # 注册事件监听
        self.event_engine.register("eTickOKX.", self._on_tick)
        self.event_engine.register("eTickBINANCE_USDT.", self._on_tick)
        
        print("✅ 行情数据服务初始化完成")
        
    def _on_tick(self, event: Event):
        """处理tick数据并广播"""
        tick: TickData = event.data
        
        # 准备广播数据
        tick_data = {
            'symbol': tick.symbol,
            'exchange': tick.exchange.value,
            'last_price': float(tick.last_price),
            'volume': float(tick.volume),
            'datetime': tick.datetime.isoformat(),
            'bid_price_1': float(tick.bid_price_1),
            'ask_price_1': float(tick.ask_price_1),
        }
        
        # 广播到消息总线
        self.message_bus.publish('market_data', tick_data)
        
        # 可选：打印日志
        if tick.datetime.second % 10 == 0:  # 每10秒打印一次
            print(f"📊 行情数据: {tick.symbol} @ {tick.last_price}")
            
    def connect_exchanges(self, exchange_settings: Dict[str, dict]):
        """连接交易所"""
        for gateway_name, setting in exchange_settings.items():
            print(f"🔗 连接交易所: {gateway_name}")
            self.main_engine.connect(setting, gateway_name)
            sleep(5)  # 等待连接建立
            
    def subscribe_symbols(self, symbols: List[str]):
        """订阅交易品种"""
        for symbol in symbols:
            print(f"📡 订阅品种: {symbol}")
            # 这里可以添加订阅逻辑
            
    def run(self):
        """运行服务"""
        print("📡 行情数据服务开始运行...")
        while True:
            sleep(1)

# ===============================
# 🧠 服务2：策略计算引擎
# ===============================

class StrategyEngine:
    """
    策略计算引擎
    - 接收行情数据
    - 统一计算技术指标
    - 生成交易信号并分发给账户管理服务
    """
    
    def __init__(self):
        print("🧠 启动策略计算引擎...")
        
        self.message_bus = MessageBus()
        self.strategies = {}  # 策略实例
        self.array_managers = {}  # 每个品种的ArrayManager
        
        # 订阅行情数据
        self.message_bus.subscribe(['market_data'], self._on_market_data)
        
        print("✅ 策略计算引擎初始化完成")
        
    def _on_market_data(self, channel: str, data: dict):
        """处理行情数据"""
        symbol = data['symbol']
        price = data['last_price']
        
        # 更新ArrayManager（这里简化，实际需要Bar数据）
        if symbol not in self.array_managers:
            self.array_managers[symbol] = ArrayManager()
            
        # 计算技术指标（示例：RSI）
        signals = self._calculate_signals(symbol, price)
        
        if signals:
            # 广播交易信号
            signal_data = {
                'symbol': symbol,
                'signals': signals,
                'datetime': datetime.now().isoformat(),
                'price': price
            }
            self.message_bus.publish('trading_signals', signal_data)
            print(f"🎯 生成交易信号: {symbol} -> {signals}")
            
    def _calculate_signals(self, symbol: str, price: float) -> List[dict]:
        """计算交易信号（示例实现）"""
        signals = []
        
        # 示例：简单的价格突破策略
        if symbol == 'BTCUSDT':
            if price > 45000:  # 示例条件
                signals.append({
                    'action': 'BUY',
                    'volume': 0.01,
                    'strategy': 'price_breakthrough'
                })
        
        return signals
        
    def add_strategy(self, strategy_name: str, strategy_config: dict):
        """添加策略"""
        self.strategies[strategy_name] = strategy_config
        print(f"📊 添加策略: {strategy_name}")
        
    def run(self):
        """运行引擎"""
        print("🧠 策略计算引擎开始运行...")
        while True:
            sleep(1)

# ===============================
# 💰 服务3：账户管理服务
# ===============================

class AccountManager:
    """
    账户管理服务
    - 每个实例管理一个交易账户
    - 接收交易信号并执行订单
    - 独立管理仓位和风险
    """
    
    def __init__(self, account_id: str, exchange_setting: dict):
        print(f"💰 启动账户管理服务: {account_id}")
        
        self.account_id = account_id
        self.message_bus = MessageBus()
        
        # 初始化HowTrader组件
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        
        # 添加对应的交易所网关
        self.main_engine.add_gateway(OkxGateway)
        self.cta_engine = self.main_engine.add_app(CtaStrategyApp)
        
        # 连接交易所
        gateway_name = list(exchange_setting.keys())[0]
        self.main_engine.connect(exchange_setting[gateway_name], gateway_name)
        
        # 订阅交易信号
        self.message_bus.subscribe(['trading_signals'], self._on_trading_signal)
        
        # 注册交易事件
        self.event_engine.register("eTrade", self._on_trade)
        self.event_engine.register("eOrder", self._on_order)
        
        self.positions = {}  # 持仓记录
        
        print(f"✅ 账户 {account_id} 管理服务初始化完成")
        
    def _on_trading_signal(self, channel: str, data: dict):
        """处理交易信号"""
        signals = data['signals']
        symbol = data['symbol']
        price = data['price']
        
        for signal in signals:
            self._execute_signal(symbol, signal, price)
            
    def _execute_signal(self, symbol: str, signal: dict, current_price: float):
        """执行交易信号"""
        action = signal['action']
        volume = signal['volume']
        
        print(f"💰 账户 {self.account_id} 执行信号: {action} {volume} {symbol} @ {current_price}")
        
        # 这里实现具体的下单逻辑
        # self.main_engine.send_order(...)
        
        # 发送执行结果到监控服务
        execution_data = {
            'account_id': self.account_id,
            'symbol': symbol,
            'action': action,
            'volume': volume,
            'price': current_price,
            'datetime': datetime.now().isoformat()
        }
        self.message_bus.publish('trade_executions', execution_data)
        
    def _on_trade(self, event: Event):
        """处理成交回报"""
        trade: TradeData = event.data
        print(f"✅ 账户 {self.account_id} 成交: {trade.symbol} {trade.volume} @ {trade.price}")
        
        # 更新持仓
        self._update_position(trade)
        
        # 发送到监控服务
        trade_data = {
            'account_id': self.account_id,
            'symbol': trade.symbol,
            'volume': float(trade.volume),
            'price': float(trade.price),
            'direction': trade.direction.value,
            'datetime': trade.datetime.isoformat()
        }
        self.message_bus.publish('account_trades', trade_data)
        
    def _on_order(self, event: Event):
        """处理订单回报"""
        order: OrderData = event.data
        print(f"📋 账户 {self.account_id} 订单更新: {order.symbol} {order.status.value}")
        
    def _update_position(self, trade: TradeData):
        """更新持仓"""
        symbol = trade.symbol
        if symbol not in self.positions:
            self.positions[symbol] = 0
            
        if trade.direction == Direction.LONG:
            self.positions[symbol] += float(trade.volume)
        else:
            self.positions[symbol] -= float(trade.volume)
            
    def run(self):
        """运行账户管理服务"""
        print(f"💰 账户 {self.account_id} 管理服务开始运行...")
        while True:
            sleep(1)

# ===============================
# 📊 服务4：监控管理服务  
# ===============================

class MonitorService:
    """
    监控管理服务
    - 监控所有服务的运行状态
    - 收集交易数据和统计信息
    - 风险控制和报警
    """
    
    def __init__(self):
        print("📊 启动监控管理服务...")
        
        self.message_bus = MessageBus()
        
        # 订阅所有监控频道
        channels = ['trade_executions', 'account_trades', 'system_status']
        self.message_bus.subscribe(channels, self._on_monitor_data)
        
        self.account_stats = {}  # 账户统计
        self.system_stats = {}   # 系统统计
        
        print("✅ 监控管理服务初始化完成")
        
    def _on_monitor_data(self, channel: str, data: dict):
        """处理监控数据"""
        if channel == 'account_trades':
            self._update_account_stats(data)
        elif channel == 'trade_executions':
            self._log_execution(data)
        elif channel == 'system_status':
            self._update_system_stats(data)
            
    def _update_account_stats(self, trade_data: dict):
        """更新账户统计"""
        account_id = trade_data['account_id']
        
        if account_id not in self.account_stats:
            self.account_stats[account_id] = {
                'total_trades': 0,
                'total_volume': 0,
                'last_trade': None
            }
            
        stats = self.account_stats[account_id]
        stats['total_trades'] += 1
        stats['total_volume'] += trade_data['volume']
        stats['last_trade'] = trade_data['datetime']
        
        print(f"📈 账户统计更新: {account_id} 累计交易 {stats['total_trades']} 笔")
        
    def _log_execution(self, execution_data: dict):
        """记录执行日志"""
        account = execution_data['account_id']
        action = execution_data['action']
        symbol = execution_data['symbol']
        print(f"📝 执行记录: 账户 {account} {action} {symbol}")
        
    def _update_system_stats(self, system_data: dict):
        """更新系统统计"""
        self.system_stats.update(system_data)
        
    def get_dashboard_data(self) -> dict:
        """获取监控面板数据"""
        return {
            'account_stats': self.account_stats,
            'system_stats': self.system_stats,
            'timestamp': datetime.now().isoformat()
        }
        
    def run(self):
        """运行监控服务"""
        print("📊 监控管理服务开始运行...")
        while True:
            # 定期输出统计信息
            sleep(30)
            dashboard = self.get_dashboard_data()
            print(f"📊 系统状态: {len(self.account_stats)} 个账户运行中")

# ===============================
# 🐳 Docker化部署配置
# ===============================

def create_docker_compose():
    """创建docker-compose.yml配置文件"""
    
    docker_compose_content = '''
version: '3.8'

services:
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
    networks:
      - howtrader-network

  market-data-service:
    build: .
    command: python -m services.market_data_service
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
    networks:
      - howtrader-network

  strategy-engine:
    build: .
    command: python -m services.strategy_engine
    depends_on:
      - redis
      - market-data-service
    environment:
      - REDIS_HOST=redis
    networks:
      - howtrader-network

  account-manager-1:
    build: .
    command: python -m services.account_manager --account-id=account_1
    depends_on:
      - redis
      - strategy-engine
    environment:
      - REDIS_HOST=redis
      - ACCOUNT_ID=account_1
    networks:
      - howtrader-network

  account-manager-2:
    build: .
    command: python -m services.account_manager --account-id=account_2
    depends_on:
      - redis
      - strategy-engine
    environment:
      - REDIS_HOST=redis
      - ACCOUNT_ID=account_2
    networks:
      - howtrader-network

  monitor-service:
    build: .
    command: python -m services.monitor_service
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
    ports:
      - "8080:8080"  # 监控面板端口
    networks:
      - howtrader-network

networks:
  howtrader-network:
    driver: bridge
'''
    
    with open('docker-compose.yml', 'w') as f:
        f.write(docker_compose_content)
        
    print("✅ Docker Compose 配置文件已创建")

# ===============================
# 🚀 主程序示例
# ===============================

def main():
    """
    演示如何使用分布式架构
    """
    print("=" * 60)
    print("🎯 HowTrader 分布式微服务架构演示")
    print("=" * 60)
    
    print("\n📋 架构优势:")
    print("✅ 行情数据统一获取，避免重复连接")
    print("✅ 策略计算集中处理，降低资源消耗")  
    print("✅ 账户管理独立部署，支持无限扩展")
    print("✅ 监控服务统一管理，实时掌控全局")
    
    print("\n🚀 部署步骤:")
    print("1. 启动 Redis 消息总线")
    print("2. 启动行情数据服务")
    print("3. 启动策略计算引擎")
    print("4. 为每个账户启动账户管理服务")
    print("5. 启动监控管理服务")
    
    print("\n🐳 Docker 部署:")
    print("docker-compose up -d")
    
    # 创建Docker配置
    create_docker_compose()
    
    print("\n💡 扩展账户:")
    print("只需复制 account-manager 服务并修改 ACCOUNT_ID")
    print("系统会自动识别新账户并开始管理")

if __name__ == "__main__":
    main() 