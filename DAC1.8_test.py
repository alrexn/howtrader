# -*- coding: utf-8 -*-
"""
Created on Fri Jul 26 18:07:41 2024

@author: Administrator
"""
import json
import threading
from threading import Thread
import time
from queue import Queue, Empty
import okx.Account as Account
import okx.PublicData as PublicData
import okx.Trade as Trade
import okx.MarketData as MarketData
from ws_subscriber import OKXWebSocketSubscriber
import logging
from datetime import datetime, timedelta
import pandas as pd
import random
from option_hedge import OKXSDKOptionHedge


# 添加期权对冲任务队列和结果队列
option_hedge_queue = Queue()
option_result_queue = Queue()
# 全局熔断事件（任何线程 set 即全局停机）
shutdown_event = threading.Event()

# 期权对冲处理线程
class OptionHedgeWorker(threading.Thread):
    def __init__(self, api_key, secret_key, passphrase,account_id):
        threading.Thread.__init__(self)
        self.stop_flag = threading.Event()
        self.hedge = OKXSDKOptionHedge(api_key, secret_key, passphrase,account_id)
        self.daemon = True
        
    def run(self):
        while not self.stop_flag.is_set() and not shutdown_event.is_set():
            try:
                # 尝试从队列获取任务，超时1秒
                task = option_hedge_queue.get(timeout=1)
                action = task.get('action')
                task_id = task.get('task_id', 'unknown')
                try:
                    if action == 'open_op':
                        add_count = task.get('add_count')
                        amount = task.get('amount')
                        self.hedge.open_op(add_count, amount)
                        logging.error(f"Option hedge opened: add_count={add_count}, amount={amount}")
                        option_result_queue.put({'task_id': task_id, 'status': 'success', 'action': action})
                    elif action == 'close_all':
                        self.hedge.close_all_positions()
                        logging.error("All option positions closed")
                        option_result_queue.put({'task_id': task_id, 'status': 'success', 'action': action})
                    elif action == 'close_put':
                        add_count = task.get('add_count')
                        amount = task.get('amount', 0)
                        self.hedge.close_put_option(add_count, amount)
                        logging.error(f"PUT option positions closed: add_count={add_count}")
                        option_result_queue.put({'task_id': task_id, 'status': 'success', 'action': action})
                    elif action == 'maintain':
                        self.hedge.maintain_positions()
                        logging.error("Option positions maintained")
                        option_result_queue.put({'task_id': task_id, 'status': 'success', 'action': action})
                    elif action == 'get_positions':
                        positions = self.hedge.get_position_option()
                        option_result_queue.put({
                            'task_id': task_id, 
                            'status': 'success', 
                            'action': action,
                            'positions': positions
                        })
                except Exception as e:
                    logging.error(f"Error executing option hedge task {action}: {e}")
                    option_result_queue.put({
                        'task_id': task_id, 
                        'status': 'error', 
                        'action': action,
                        'error': str(e)
                    })
                option_hedge_queue.task_done()
            
            except Empty:
                # 队列为空，继续等待
                pass
            except Exception as e:
                logging.error(f"Error in option hedge worker: {e}")
                time.sleep(5)
    
    def stop(self):
        self.stop_flag.set()

# 查询期权仓位的工具函数，供主程序和策略调用
def get_option_positions():
    """
    查询当前期权仓位，返回包含期权合约和仓位大小的字典
    """
    task_id = f"get_positions_{int(time.time())}"
    option_hedge_queue.put({
        'action': 'get_positions',
        'task_id': task_id
    })
    
    # 等待结果，最多等待5秒
    start_time = time.time()
    while time.time() - start_time < 5:
        try:
            result = option_result_queue.get(timeout=1)
            if result.get('task_id') == task_id:
                option_result_queue.task_done()
                if result.get('status') == 'success':
                    return result.get('positions', {})
                else:
                    logging.error(f"Error getting option positions: {result.get('error')}")
                    return {}
        except Empty:
            pass
    
    logging.error("Timeout waiting for option positions")
    return {}

class MartingaleStrategy(threading.Thread):
    def __init__(self, currency, strategy_params, output_queue, command_queue, restart=0, wait=True, option=False):
        threading.Thread.__init__(self)
        self.currency = currency
        self.strategy_params = strategy_params
        self.stop_flag = threading.Event()
        self.output_queue = output_queue
        self.command_queue = command_queue
        self.restart=restart
        self.stop_event=wait
        self.sellpx=None
        self.maxsz=0
        self.avgPx=0
        # 添加期权对冲相关属性
        self.option = option  # 是否开启期权对冲，可从外部传入
        self.last_maintenance_time = None  # 添加最后维护时间记录

    def run(self):
        self.instId=self.currency+'-USDT-SWAP'
        ##初始化卖单编号、参数
        if self.strategy_params['mode'] == 1:
             self.sellid='sell'+self.currency+'ksutilx1'
             self.posSide='long'
             self.side='buy'
             print(f"{self.currency} thread:正在做多执行交易")
        elif self.strategy_params['mode'] == 2:
            self.sellid='buy'+self.currency+'ksutilx2'
            self.posSide='short'
            self.side='sell'
            print(f"{self.currency} thread:正在做空执行交易")
        x=True
        while x:
            try:
                swapresult = publicDataAPI.get_instruments(instType="SWAP", instId=self.instId)
                ctVal = float(swapresult["data"][0]["ctVal"])
                minSz = float(swapresult["data"][0]["minSz"])
                tickSz = float(swapresult["data"][0]["tickSz"])
                minSz_str1 = str(minSz)
                if '.' in minSz_str1:
                    d_minSz = len(minSz_str1.split('.')[1])
                    if int(minSz_str1.split('.')[1]) == 0:         
                        d_minSz = 0
                else:
                    d_minSz = 0
                x=False
            except Exception as e:
               logging.error(f"Error {self.currency} get_instrument_info: {e}")
               time.sleep(1)
        #设置杠杆或恢复订单编号
        result = accountAPI.set_leverage(instId=self.instId,lever=self.strategy_params['lever'],mgnMode="cross")
        print('lever:',result['data'][0])
        if self.restart==0:
            self.open_position(ctVal, minSz,d_minSz,tickSz,0)
        else:
            self.orders, cost = martingale_strategy(self.strategy_params, 1)
            for i in range(1, self.strategy_params['adding_number']+1):
                if self.strategy_params['mode'] == 1:
                    self.orders[i]['olderid']=self.orders[i]['olderid']+self.currency+'ksutilx1'
                elif self.strategy_params['mode'] == 2:
                    self.orders[i]['olderid']=self.orders[i]['olderid']+self.currency+'ksutilx2' 
        position=self.get_position_info(minSz)
        self.avgPx=avgPx=float(position['avgPx'])
        self.maxsz=round(100000/(avgPx*ctVal),d_minSz)
        self.last_log_time = datetime.now()
        print(f"{self.currency} thread:恢复成功!")
        self.last_log_time = datetime.now()
        
        while not self.stop_flag.is_set() and not shutdown_event.is_set():
            try:
                self.trade(ctVal,minSz,d_minSz,tickSz)
                self.process_commands(minSz)
                time.sleep(1.5)
            except Exception as e:
                logging.error(f"Error in {self.currency} thread: {e}")
                time.sleep(5)
                
    def trade(self, ctVal, minSz,d_minSz,tickSz):
        position=self.get_position_info(minSz)
        pos = float(position['pos'])
        avgPx = float(position['avgPx'])
        if  pos==minSz:
            self.cancel_openorder()
            if self.option:
                # 通过队列请求关闭所有期权头寸
                task_id = f"close_all_{self.currency}_{int(time.time())}"
                option_hedge_queue.put({
                    'action': 'close_all',
                    'task_id': task_id
                })
                self.option=False
                # 通知主线程更新状态
                action={
                    'ccy': self.currency,
                    'mode': self.strategy_params['mode'],
                    'action': 'update_option',
                    'option': False
                }
                self.output_queue.put(action)
            if self.stop_event:
                self.open_position(ctVal, minSz, d_minSz,tickSz,1)
            else:
                action={
                    'ccy':self.currency,
                    'mode':self.strategy_params['mode'],
                    'action':'stop'
                    }
                self.stop()
                self.close_position()
                self.output_queue.put(action)
                return
        else: 
            olderresult=get_order_with_lock(self.instId,self.sellid)
            if olderresult is None:
                # 卖单丢失 → 触发自修复
                self.ensure_sell_order(pos, minSz, d_minSz, avgPx, tickSz)
            else:
                # 卖单仍在，继续原有逻辑
                sellpos = round(float(olderresult["data"][0]["sz"]), d_minSz)
                state   = olderresult["data"][0]["state"]
                avPos   = round(pos - sellpos, d_minSz)

                if avPos > minSz or state in ("filled", "canceled"):
                    amount = round(min(pos - minSz, self.maxsz), d_minSz)
                    if sellpos < amount or state in ("filled", "canceled") or avgPx != self.avgPx:
                        try:
                            tradeAPI.cancel_order(instId=self.instId,
                                                  clOrdId=self.sellid)
                        except Exception as e:
                            logging.error(f"{self.currency}: 取消旧卖单失败 -> {e}")
                        self.sell_order(avgPx, amount)
                        self.avgPx = avgPx
    
        # 记录每小时的日志，修复卖单仓位异常
        current_time = datetime.now()
        if current_time >= self.last_log_time + timedelta(hours=1):
            self.last_log_time = current_time          # 更新时间戳
            older = get_order_with_lock(self.instId, self.sellid)
            # 输出巡检日志：不论卖单是否存在都打一次
            sell_state = older["data"][0]["state"] if older else "missing"
            sell_sz    = round(float(older["data"][0]["sz"]), d_minSz) if older else 0
            logging.error(
                f"sell-order-check | {self.currency} | pos={pos} "
                f"sellSz={sell_sz} state={sell_state} minSz={minSz}"
            )
            # 条件：1) 卖单不存在 2) 卖单状态 filled/canceled 且仍有持仓
            if (older is None or sell_state in ("filled", "canceled")) and pos > minSz:
                logging.error(f"{self.currency}: 卖单缺失 or 已成，执行自修复")
                self.ensure_sell_order(pos, minSz, d_minSz, avgPx, tickSz)  
            # 每天检查期权是否需要展期
            if self.option:
                # 检查是否需要执行维护（每天一次）
                if (self.last_maintenance_time is None or 
                    (current_time - self.last_maintenance_time).total_seconds() >= 24 * 3600):
                    # 通过队列请求维护期权头寸
                    task_id = f"maintain_{self.currency}_{int(time.time())}"
                    option_hedge_queue.put({
                        'action': 'maintain',
                        'task_id': task_id
                    })
                    self.last_maintenance_time = current_time
                    
    def stop(self):
        print(f"stop:{self.currency}")
        self.stop_flag.set()
        if self.stop_flag.is_set():
            print('stop已经设置线程停止')
            
    def process_commands(self,minSz):
        try:
            # 检查队列是否为空
            try:
                command = self.command_queue.get_nowait()
            except Empty:
                return  # 如果队列为空，不做任何处理
            print(f"{self.currency} process_commands action: {command['action']} ")
            if command['action']=='close_position':
                self.stop()
                self.close_position()
                return
            elif command['action']=='close_wait':
                self.stop_event=False
            elif command['action']=='status':
                position=self.get_position_info(minSz)
                instId = position['instId']
                lever=int(position['lever'])
                notionalUsd = float(position['notionalUsd']) if position.get('notionalUsd', "") != "" else 0.0
                realizedPnl = float(position['realizedPnl']) if position.get('realizedPnl', "") != "" else 0.0
                upl = float(position['upl']) if position.get('upl', "") != "" else 0.0
                liqPx = float(position['liqPx']) if position.get('liqPx', "") != "" else 0.0
                last = float(position['last']) if position.get('last', "") != "" else 0.0
                max_raise=liqPx/last-1
                message={
                    'instId':instId,
                    'lever':lever,
                    'posside':self.posSide,
                    '仓位':notionalUsd,
                    '已实现收益':realizedPnl,
                    '未实现收益':upl,
                    '强平价':liqPx,
                    '距离强平涨跌幅':max_raise,
                    }
                action={
                    'ccy':self.currency,
                    'mode':self.strategy_params['mode'],
                    'action':'status',
                    'message':message
                    }
                self.output_queue.put(action)
        except Exception as e:
            logging.error(f"{self.currency} process_commands: {e}")
            pass
          
    def open_position(self,ctVal,minSz,d_minSz,tickSz,mode):
         try:
             decimal_places=d_minSz
             factor = 10 ** decimal_places
             pxreslut = marketDataAPI.get_ticker(instId=self.instId)
             swappx = float(pxreslut['data'][0]['askPx'])
             if mode == 0:
                  swapsz = float(self.strategy_params['first_margin'] * self.strategy_params['lever'] / (ctVal * swappx))
                  swapsz = int((swapsz+minSz) * factor)/ factor
                  v=swapsz-minSz
             elif mode == 1:
                  swapsz = float(self.strategy_params['first_margin'] * self.strategy_params['lever'] / (ctVal * swappx))
                  swapsz = int(swapsz * factor) / factor
                  v=swapsz
             # 开仓第一笔
             if swapsz<minSz:
                logging.error(f"Error {self.currency}  have no enough margin")
                print(f"Error {self.currency}  have no enough margin")
                self.stop()
                return
             if self.strategy_params['mode'] == 1:
                  traderesult = tradeAPI.place_order(instId=self.instId, tdMode="cross", side="buy", posSide='long', ordType="market", sz=swapsz)
             elif self.strategy_params['mode'] == 2:
                  traderesult = tradeAPI.place_order(instId=self.instId, tdMode="cross", side="sell", posSide='short', ordType="market", sz=swapsz)
             # 生成买卖挂单
             ordId = traderesult['data'][0]['ordId']
             olderresult = get_order_with_lock(self.instId,' ',ordId)
             px1=px= float(olderresult['data'][0]['avgPx'])
             if self.sellpx is not None:
                 slippage= abs(self.sellpx-px)
                 if slippage>=20*tickSz:
                     px1=self.sellpx
             self.orders, cost = martingale_strategy(self.strategy_params, px1, ctVal, minSz)
             #挂平仓
             if mode == 0:
                 swapsz=round(swapsz-minSz,d_minSz)
                 if  swapsz<minSz: 
                         swapsz=minSz
             if self.strategy_params['mode'] == 1:
                 px = px*(1+self.strategy_params['profit_target'])
                 self.sellpx=float(px)
                 f_px = f"{px:.15f}"
                 traderesult1 = tradeAPI.place_order(instId=self.instId, tdMode="cross",clOrdId=self.sellid, side="sell", posSide='long', ordType="limit", px=f_px, sz=swapsz)
             elif self.strategy_params['mode'] == 2:
                 px = px*(1-self.strategy_params['profit_target'])
                 self.sellpx=float(px)
                 f_px = f"{px:.15f}"
                 traderesult1 = tradeAPI.place_order(instId=self.instId, tdMode="cross",clOrdId=self.sellid, side="buy", posSide='short', ordType="limit", px=f_px, sz=swapsz)
             #挂开仓
             k=0
             place_orders=[]
             for i in range(1, self.strategy_params['adding_number']+1):
                  order = self.orders[i]  # 引用订单字典对象
                  k +=1
                  if self.strategy_params['mode'] == 1:    
                          swapsz = order['older_pos']
                          px =float(order['older_price'])-random.randint(int(i/2),i)*tickSz
                          f_px = f"{px:.15f}"
                          orderid=order['olderid']+self.currency+'ksutilx1'
                          order_place={"instId": self.instId, "tdMode": "cross", "clOrdId": orderid, "side": "buy", "posSide":"long" ,"ordType": "limit", "px":f_px,"sz":swapsz }
                          place_orders.append(order_place)
                          if k==20:
                               result_place = tradeAPI.place_multiple_orders(place_orders)
                               k=0
                               place_orders=[]
                          order['olderid'] = orderid  # 直接修改引用的字典对象
                          v += float(swapsz)
                  elif self.strategy_params['mode'] == 2:
                           swapsz = order['older_pos']
                           px = float(order['older_price'])+random.randint(int(i/2),i)*tickSz
                           f_px = f"{px:.15f}"
                           orderid=order['olderid']+self.currency+'ksutilx2'
                           order_place={"instId": self.instId, "tdMode": "cross", "clOrdId": orderid, "side": "sell", "posSide":"short" ,"ordType": "limit", "px":f_px,"sz":swapsz }
                           place_orders.append(order_place)
                           if k==20:
                                result_place = tradeAPI.place_multiple_orders(place_orders)
                                k=0
                                place_orders=[]
                           order['olderid'] = orderid  # 直接修改引用的字典对象
                           v += float(swapsz)  
             result_place = tradeAPI.place_multiple_orders(place_orders)
         except Exception as e:
            logging.error(f"Error {self.currency} open_position: {e}")
            time.sleep(3)
            logging.error(f"Error {self.currency} open_position repairing")
            self.cancel_openorder()
            self.re_position(minSz, d_minSz)
            
    def get_position_info(self,minSz):
         retry_count=0
         x = True
         while x:
             try:
                 position_info = subscriber.get_position_info()
                 if position_info is None:
                     raise ValueError("Position info is None")
                 for i in position_info:
                     if i['instId'] == self.instId and i.get('pos'):
                         if i['posSide'] == 'long' and self.strategy_params['mode'] == 1:
                             x = False
                             return i
                         elif i['posSide'] == 'short' and self.strategy_params['mode'] == 2:
                             x = False
                             return i
                 else:
                     retry_count +=1
                     if retry_count >= 100:
                          logging.error(f"Error {self.currency} no macthing position info")
                          if self.strategy_params['mode'] == 1:
                               traderesult = tradeAPI.place_order(instId=self.instId, tdMode="cross", side="buy", posSide='long', ordType="market", sz=minSz)
                          elif self.strategy_params['mode'] == 2:
                               traderesult = tradeAPI.place_order(instId=self.instId, tdMode="cross", side="sell", posSide='short', ordType="market", sz=minSz)
                          logging.error(f"Error {self.currency} re-position info {traderesult} ")
                     time.sleep(0.5)
             except Exception as e:
                 print(f"Error {self.currency} getting position info: {e}")
                 time.sleep(2)
                 continue

    def restart_strategy(self):
         self.restart +=1
         self.__init__(self.currency, self.strategy_params, self.output_queue, self.command_queue, self.restart, self.stop_event, self.option)
         self.start()
         
    def close_position(self):
         x=True
         while x:
             try:
                 result = tradeAPI.cancel_order(instId=self.instId, clOrdId = self.sellid)
                 logging.error(f"close_position:{self.currency} {result}")
                 self.cancel_openorder()
                 logging.error(f"close_position:{self.currency} cancel_openorder 已执行")
                 # 如果开启了期权对冲，先平掉期权头寸
                 if self.option:
                     # 通过队列请求关闭所有期权头寸
                     task_id = f"close_all_{self.currency}_{int(time.time())}"
                     option_hedge_queue.put({
                         'action': 'close_all',
                         'task_id': task_id
                     })
                     self.option = False
                 result = tradeAPI.close_positions(instId=self.instId,mgnMode="cross",posSide=self.posSide)
                 logging.error(f"close_position:{self.currency} {result}")
                 x=False
                 logging.error(f"close_position:{self.currency} 已执行")
                 print("平仓已完成")
             except Exception as e:
                 logging.error(f"Error {self.currency} close_position: {e}")
        
    def cancel_openorder(self):
        """
        取消所有加仓买单（开仓单）
        注意：此方法只取消买单，不取消卖单(sellid)
        卖单需要单独通过 tradeAPI.cancel_order(clOrdId=self.sellid) 取消
        """
        try:
            if not hasattr(self, 'orders') or not self.orders:
                logging.error(f"{self.currency}: orders未初始化，无法取消订单")
                return
                
            cancel_orders=[]
            k=0
            for i in self.orders[1:]:
                k+=1
                order={"instId": self.instId,"clOrdId":i['olderid']}
                cancel_orders.append(order)
                if k==20:
                    cancel_order = tradeAPI.cancel_multiple_orders(cancel_orders)
                    cancel_orders=[]
                    k=0
            cancel_order = tradeAPI.cancel_multiple_orders(cancel_orders)
        except Exception as e:
           logging.error(f"Error {self.currency} cancel_openorder: {e}")
            
       # ============ 1) 触发期权对冲的封装 ============
    def _open_option_hedge(self, add_count: int, op_amount: float) -> None:
        """把开启对冲的消息丢进队列，并同步 UI 状态"""
        self.option = True
        task_id = f"open_op_{self.currency}_{int(time.time())}"
        option_hedge_queue.put({
            "action": "open_op",
            "add_count": add_count,
            "amount": op_amount,
            "task_id": task_id
        })
        # 把状态同步给主线程
        self.output_queue.put({
            "ccy": self.currency,
            "mode": self.strategy_params["mode"],
            "action": "update_option",
            "option": True
        })


    # ============ 2) 计算 profit + 期权触发 ============
    def _calc_profit_and_maybe_open_option(self,amount) -> float:
        """
        分层决定 profit 系数，并在 15 / 21 / 25 加仓处打开期权对冲
        返回：profit 系数 (做多用 avgPx*profit；做空用 avgPx*(1-profit))
        """
        # 统计当前"加仓挂单"数量
        count = sum(
            1 for od in tradeAPI.get_order_list(instType="SWAP", instId=self.instId)["data"]
            if od["posSide"] == self.posSide and od["side"] == self.side
        )
        add_count = int(self.strategy_params['adding_number'])-int(count)
        profit = 1 + self.strategy_params["profit_target"]   # 默认                                        # 你原来 op_const
        fm     = self.strategy_params["first_margin"]

        if add_count>=5 and add_count<8:
              profit=1.002
        elif add_count>=8 and add_count<10:
               profit=1.005
               if add_count==8 :
                   self._open_option_hedge(add_count, 10)   
        elif add_count==10:
               profit=1.02    
               if add_count == 10:
                   self._close_put_option(add_count)
        return profit

    # ============ 4) 平仓PUT期权的封装 ============
    def _close_put_option(self, add_count: int) -> None:
        """把平仓PUT期权的消息丢进队列"""
        task_id = f"close_put_{self.currency}_{int(time.time())}"
        option_hedge_queue.put({
            "action": "close_put",
            "add_count": add_count,
            "task_id": task_id
        })
        logging.error(f"{self.currency}: 发送平仓PUT期权指令，加仓次数={add_count}")

    # ============ 3) 带价格带兜底的下单 ============
    def _place_limit_then_fallback_market(self, side: str, posSide: str,
                                      amount: float, px: float) -> None:
        
        """
         先挂 LIMIT；遇到 51015 (duplicate) → 先 cancel 再重试
         其余非 0 码或异常 → Fallback MARKET
         """
        def _place_limit():
            return tradeAPI.place_order(
                 instId=self.instId,
                 tdMode="cross",
                 clOrdId=self.sellid,            # **固定 ID 不变**
                 side=side, posSide=posSide,
                 ordType="limit", px=f"{px:.15f}", sz=amount
             )
        try:
             res = _place_limit()
             code = res["data"][0]["sCode"]
             if code == "0":                         # 成功
                 return
             if code == "51015":                     # duplicate clOrdId
                 tradeAPI.cancel_order(instId=self.instId, clOrdId=self.sellid)
                 res = _place_limit()                # 再试一次
                 if res["data"][0]["sCode"] == "0":
                     return
             # 其它错误直接 fallback MARKET
             tradeAPI.place_order(instId=self.instId, tdMode="cross",
                                  side=side, posSide=posSide,
                                  ordType="market", sz=amount)
             logging.error(f"{self.currency}: LIMIT 被拒→MARKET 平 {amount} (code={code})")
        except Exception as e:
             logging.error(f"{self.currency}: LIMIT 异常 {e}→MARKET")
             try:
                 tradeAPI.place_order(instId=self.instId, tdMode="cross",
                                      side=side, posSide=posSide,
                                      ordType="market", sz=amount)
             except Exception as e2:
                 logging.error(f"{self.currency}: MARKET 兜底失败 {e2}")
   
    
    def sell_order(self, avgPx: float, amount: float) -> None:
        """
        · 计算 profit & 期权触发（_calc_profit_and_maybe_open_option）
        · 直接限价挂单，极快；被拒再兜底 MARKET
        """
        try:
            profit = self._calc_profit_and_maybe_open_option(amount)

            if self.strategy_params["mode"] == 1:      # 做多 → 平多
                limit_px = avgPx * profit
                side, posSide = "sell", "long"
            else:                                      # 做空 → 平空
                limit_px = avgPx * (1 - profit)
                side, posSide = "buy",  "short"

            self.sellpx = limit_px
            self._place_limit_then_fallback_market(side, posSide, amount, limit_px)

        except Exception as e:
            logging.error(f"{self.currency}: sell_order 异常 -> {e}")
    
    def ensure_sell_order(self,pos: float,minSz: float,d_minSz: int,avgPx: float,tickSz: float) -> None:
        """
        ・ 如果 sellid 仍 live / partial → 什么都不做
        ・ 如果 sellid 不存在 / 已成交 / 已取消 → 取消残迹 → 重挂
        """
        if self.stop_flag.is_set():
            return

        older = get_order_with_lock(self.instId, self.sellid)
        live  = older and older["data"][0]["state"] in ("live", "partially_filled")

        if live:                                           # 卖单仍在
            return

        # 1) 幂等式取消（即便已经 canceled 也不会报错）
        try:
            tradeAPI.cancel_order(instId=self.instId, clOrdId=self.sellid)
        except Exception:
            pass  # 可能已经不在，无需理会
        
        # 2) 重新计算应卖数量 - 修复逻辑错误，与trade方法保持一致
        if pos <= minSz:
            logging.error(f"{self.currency}: 仓位{pos}小于等于最小仓位{minSz}，无法挂卖单")
            return
            
        amount = round(min(pos - minSz, self.maxsz), d_minSz)
        if amount < minSz:
            logging.error(f"{self.currency}: 计算的卖单数量{amount}小于最小仓位{minSz}，跳过")
            return  # 实际无足够仓位可平
        
        logging.error(f"{self.currency}: 平仓卖单缺失，自愈 amount={amount}")
        # 3) 调用业务层卖单函数（内部会自动 price-fallback）
        self.sell_order(avgPx, amount)
     
    def re_position(self,minSz,d_minSz):
         try:
             olderresult=get_order_with_lock(self.instId,self.sellid)
             state=olderresult['data'][0]['state']
             position=self.get_position_info(minSz)
             pos = float(position['pos'])
             avgPx = float(position['avgPx'])
             uplRatio=float(position['uplRatio'])
             lastpx=float(position['last'])
             amount=round(pos-minSz,d_minSz)
             self.sellpx=None
             if (state=='filled' or state=='canceled') and pos>minSz:
                 if self.strategy_params['mode']==1 :
                     if  uplRatio>=self.strategy_params['profit_target']:
                         try:
                             result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",side="sell",posSide='long',ordType="market",sz=amount)
                         except Exception as e:
                             logging.error(f"Error {self.currency} re_position placing sell order: {e}")
                     else:
                         px=avgPx*(1+self.strategy_params['profit_target'])
                         if lastpx>px:
                                 px=lastpx
                         f_px = f"{px:.15f}"
                         try:
                             result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",clOrdId=self.sellid,side="sell",posSide='long',ordType="limit",px=f_px,sz=amount)
                         except Exception as e:
                             logging.error(f"Error {self.currency}  re_position placing sell order: {e}")
                 elif  self.strategy_params['mode']==2:
                       if  uplRatio>=self.strategy_params['profit_target']:
                            try:
                                 result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",side="buy",posSide='short',ordType="market",sz=amount)
                            except Exception as e:
                                 logging.error(f"Error {self.currency} re_position  placing sell order: {e}")
                       else:
                           px=avgPx*(1-self.strategy_params['profit_target'])
                           if lastpx<px:
                                    px=lastpx
                           f_px = f"{px:.15f}"
                           try:
                               result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",clOrdId=self.sellid,side="buy",posSide='short',ordType="limit",px=f_px,sz=amount)
                           except Exception as e:
                               logging.error(f"Error {self.currency} re_position  placing sell order: {e}")
             elif pos>minSz and amount>=minSz:
                 try:
                     cancel_result = tradeAPI.cancel_order(instId=self.instId, clOrdId = self.sellid)
                 except Exception as e:
                     logging.error(f"Error {self.currency} re_position cancelling sell order: {e}")

                 position=self.get_position_info(minSz)
                 pos = float(position['pos'])
                 avgPx = float(position['avgPx'])
                 amount=round(pos-minSz,d_minSz)
                 if self.strategy_params['mode']==1 and pos>minSz and amount>= minSz:
                     try:
                         result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",side="sell",posSide='long',ordType="market",sz=amount)
                     except Exception as e:
                         logging.error(f"Error {self.currency} re_position placing sell order: {e}")
                 elif  self.strategy_params['mode']==2 and pos>minSz and amount>= minSz: 
                     try:
                         result_sell = tradeAPI.place_order(instId=self.instId,tdMode="cross",side="buy",posSide='short',ordType="market",sz=amount)
                     except Exception as e:
                         logging.error(f"Error {self.currency} re_position placing sell order: {e}")
                
         except Exception as e:
            logging.error(f"Error {self.currency} re_position: {e}")
       
def martingale_strategy(parameter, initial_price,ctVal=1,minSz=1):
    lever=parameter['lever']
    first_margin=parameter['first_margin']
    first_margin_add=parameter['first_margin_add']
    adding_number=parameter['adding_number']
    amount_multiplie=parameter['amount_multiplie']
    price_multiple=parameter['price_multiple']
    profit_target=parameter['profit_target']
    mode=parameter['mode']
    
    initial_price=initial_price   #实际中用实时价格
    minSz_str = str(minSz)
    if '.' in minSz_str:
       decimal_places = len(minSz_str.split('.')[1])
       if int(minSz_str.split('.')[1]) == 0:
                decimal_places = 0
    else:
       decimal_places = 0
    factor = 10 ** decimal_places
    total_pos=first_margin*lever/(initial_price*ctVal)
    total_pos = int(total_pos * factor) / factor
    average_cost=initial_price
    total_margin=first_margin
    for i in range(0,adding_number):
         total_margin +=first_margin_add*amount_multiplie**i
    if mode== 1:
          opp_ratio=parameter['opp_ratio']
          profit_price=average_cost*(1+profit_target)
          burst_price=0 if total_margin>=total_pos*average_cost*ctVal else average_cost * (1 - total_margin / (total_pos * average_cost*ctVal))
    elif mode ==2:
        opp_ratio=-parameter['opp_ratio']
        profit_price=average_cost*(1-profit_target)
        burst_price=1000000 if total_margin>=total_pos*initial_price*ctVal else (1+total_margin/(total_pos*average_cost*ctVal))*average_cost
    orders=[
         { 'older_price':initial_price,
           'older_margin':first_margin,
           'average_cost':average_cost,
           'older_pos':total_pos,
           'total_pos':total_pos,
           'burst_price':burst_price,
           'profit_price':profit_price,
           'olderid':'1',
          }    
         ]
    before_price=initial_price
    if mode == 1:
         for i in range(0,adding_number):
             k=i+1
             older_price=before_price*(1-opp_ratio*price_multiple**i)
             older_margin=first_margin_add*amount_multiplie**i
             older_pos=older_margin*lever/(older_price*ctVal)
             if k<=25:
                 older_pos = int(older_pos * factor) / factor
             else:
                 older_pos = round(minSz,decimal_places)
                 older_pos = max(minSz,older_pos)
             average_cost =(average_cost*total_pos+older_pos*older_price)/(total_pos+older_pos)
             total_pos=total_pos+older_pos
             burst_price=0 if total_margin>=total_pos*average_cost*ctVal else average_cost * (1 - total_margin / (total_pos * average_cost*ctVal))
             profit_price=average_cost*(1+profit_target)
             if burst_price >=older_price:
                     print('警告：强平线小于加仓线')
             order={
                'older_price':older_price,
                'older_margin':older_margin,
                'average_cost':average_cost,
                'older_pos':older_pos,
                'total_pos':total_pos,
                'burst_price':burst_price,
                'profit_price':profit_price,
                'olderid':str(k),
                }  
             orders.append(order)
             before_price=older_price
    elif mode == 2:
      for i in range(0,adding_number):
          k=i+1
          older_price=before_price*(1-opp_ratio*price_multiple**i)
          older_margin=first_margin_add*amount_multiplie**i
          older_pos=older_margin*lever/(older_price*ctVal)
          if k<=25:
              older_pos = int(older_pos * factor) / factor
          else:
              older_pos = round(minSz,decimal_places)
              older_pos=max(minSz,older_pos)
          average_cost =(average_cost*total_pos+older_pos*older_price)/(total_pos+older_pos)
          total_pos=total_pos+older_pos
          burst_price=1000000 if total_margin>=total_pos*average_cost*ctVal else (1+total_margin/(total_pos*average_cost*ctVal))*average_cost
          profit_price=average_cost*(1-profit_target)
          if burst_price <= older_price :
              print('警告：强平线小于加仓线')
          order={
             'older_price':older_price,
             'older_margin':older_margin,
             'average_cost':average_cost,
             'older_pos':older_pos,
             'total_pos':total_pos,
             'burst_price':burst_price,
             'profit_price':profit_price,
             'olderid':str(k),
           }  
          orders.append(order)
          before_price=older_price
    return orders,total_margin*2.1

def get_order_with_lock(instId, clOrdId, ordId='1'):
    retry_count = 0
    success = False
    while not success and not shutdown_event.is_set():
        try:
            with global_lock:
                if ordId == '1':
                    result = tradeAPI.get_order(instId=instId, clOrdId=clOrdId)
                else:
                    result = tradeAPI.get_order(instId=instId, ordId=ordId)
                if result.get('data'):
                    time.sleep(0.05)  # 控制api访问速率
                    success = True
                    return result  # 成功获取数据，返回结果
        except Exception as e:
            logging.error(f"Error {instId} while getting order: {e}")
            if "ConnectionTerminated" in str(e):
                time.sleep(2**retry_count)  # 指数回退等待时间
            else:
                time.sleep(1)  # 默认等待时间
            retry_count += 1 
            if retry_count > 5:
                msg = f"*** CRITICAL *** {instId} REST 连续失败 {retry_count} 次，停止所有策略"
                print(msg)
                logging.critical(msg)
                shutdown_event.set()
                # 抛出特定的异常而不是通用RuntimeError
                raise ConnectionError(f"API连接失败超过重试限制: {instId}")
    # 如果因为shutdown_event退出循环或其他情况，返回None
    return None

def input_strategy_params():
    params = {}
    while True:
        try:
            params['opp_ratio'] = float(input("Enter opp_ratio: ").strip())
            params['profit_target'] = float(input("Enter profit_target: ").strip())
            params['lever'] = int(input("Enter lever: ").strip())
            params['first_margin'] = float(input("Enter first_margin: ").strip())
            params['first_margin_add'] = float(input("Enter first_margin_add: ").strip())
            params['adding_number'] = int(input("Enter adding_number: ").strip())
            params['amount_multiplie'] = float(input("Enter amount_multiplie: ").strip())
            params['price_multiple'] = float(input("Enter price_multiple: ").strip())
            params['mode'] = int(input("Enter mode: ").strip())
            # 假设 martingale_strategy 是一个已经定义的函数，并返回订单和费用
            order, cost = martingale_strategy(params, 1)
            print(f"The params will tot_cost {cost} /n")
            cost =cost*0.48
            print(f"The params will net_cost {cost}")
            
            x = input("If the params are correct, please enter YES, else NOT: ").strip().upper()
            if x == 'YES':
                break
            else:
                print("Parameters are incorrect. Please re-enter.")
        except ValueError as e:
            print(f"Invalid input: {e}. Please re-enter.")
            params = {}
    return params


def get_balance():
    global Balanceamt,Balance
    Balance=accountAPI.get_account_balance(ccy='USDT')
    Balanceamt=float(Balance['data'][0]['details'][0]['availBal'])-rebalance
    print("交易账户余额：",Balanceamt)


def save_strategy_params(strategies, account_id):
    # 将 strategies 转换为列表形式
    data = [
        {
            'name': strategy_item['name'],
            'strategy_params': strategy_item['strategy'].strategy_params,
            'restart': strategy_item['strategy'].restart,
            'wait': strategy_item['strategy'].stop_event,
            'option': strategy_item['strategy'].option  # 保存期权对冲状态
        }
        for strategy_item in strategies
    ]
    file_name = f'strategies_{account_id}.json'
    with open(file_name, 'w') as f:
        json.dump(data, f)

def load_strategy_params(account_id):
    file_name = f'strategies_{account_id}.json'
    try:
        with open(file_name, 'r') as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        return []

def restore_strategies(strategies, output_queue, command_queues, account_id):
    data = load_strategy_params(account_id)
    for item in data:
        currency = item['name']
        strategy_params = item['strategy_params']
        restart = item['restart']+1
        command_queue = Queue()
        wait = item['wait']
        option = item.get('option', False)  # 恢复期权对冲状态，默认为False
        strategy = MartingaleStrategy(currency, strategy_params, output_queue, command_queue, restart, wait, option)
        # 将策略添加到 strategies 列表中
        strategies.append({
            'name': currency,
            'strategy': strategy,
            'command_queue': command_queue,
            'mode': strategy_params['mode']
        })
        command_queues[currency] = command_queue
        strategy.start()
        print(f"Restored strategy for {currency} with params {strategy_params}")     
    if len(strategies)>0:
        return strategies
    else:
        return []
    
def input_ccy_mode():
    while True:
        try:
            currency = input("Enter the currency: ").strip().upper()
            mode=int(input("Enter mode: ").strip())
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please re-enter.")
    return currency,mode

def main():
    def print_from_queue():
        global message_status,status_info
        while not shutdown_event.is_set():
            try:
                message = output_queue.get(timeout=1)  # 添加超时，定期检查shutdown事件
            except Empty:
                continue  # 超时则继续检查shutdown事件
                
            print(message)
            for strategy_item in strategies:
                if strategy_item['name'] == message['ccy'] and strategy_item['mode']==message['mode'] :
                    if message['action']=='stop':
                        strategy_item['strategy'].join()
                        if not strategy_item['strategy'].is_alive():
                            strategies.remove(strategy_item)
                            del command_queues[message['ccy']]
                            save_strategy_params(strategies, account_id)  # 保存参数信息
                            print(f"Stopped strategy for { message['ccy']} {message['mode']}.")
                        else:
                            print(f"Stopped strategy for { message['ccy']} {message['mode']} 出错")
                    elif message['action']=='status':
                        message_status.append(message['message'])
                    # 处理期权状态更新
                    elif message['action']=='update_option':
                        strategy_item['strategy'].option = message['option']
                        save_strategy_params(strategies, account_id)  # 保存参数信息
                        print(f"Updated option status for {message['ccy']} {message['mode']} to {message['option']}")
            if len(message_status)==len(strategies) and len(strategies)>=1:
                get_balance()
                status_info=pd.DataFrame(message_status)
                print(status_info)
                message_status=[]
        
        # shutdown事件被设置，打印线程退出
        logging.critical("Print thread exiting due to shutdown event")
                
    strategies = []  # 修改为列表
    output_queue = Queue()
    command_queues = {}
    
    # 启动期权对冲工作线程
    option_worker = OptionHedgeWorker(apikey, secretkey, passphrase,account_id)
    option_worker.start()
    strategies=restore_strategies(strategies, output_queue, command_queues, account_id)
    # 启动打印线程
    printer_thread = Thread(target=print_from_queue)
    printer_thread.daemon = True  # 设置为守护线程，这样主线程退出时打印线程也会退出
    printer_thread.start()
   
    try:
        while True:
            command = input("Enter command: ").strip().lower()
            if command == 'exit':
                shutdown_event.set()  # 修复：应该set而不是is_set
                for strategy_item in strategies:
                    strategy_item['strategy'].stop()
                for strategy_item in strategies:
                    strategy_item['strategy'].join()
                try:
                    if subscriber:
                        subscriber.stop()
                except Exception as e:
                    logging.error(f"停止WebSocket失败: {e}")
                # 停止期权对冲工作线程
                try:
                    option_worker.stop()
                    option_worker.join(timeout=1)  # 设置超时防止程序卡住
                except Exception as e:
                    logging.error(f"停止期权对冲线程失败: {e}")
                break
            elif command == 'start':
                currency = input("Please enter the currency: ").strip().upper()
                print(f"Currency {currency} received. Please enter strategy parameters.")
                strategy_params = input_strategy_params()
                command_queue = Queue()
                strategy = MartingaleStrategy(currency, strategy_params, output_queue, command_queue)
                # 将新策略添加到 strategies 列表中
                strategies.append({
                    'name': currency,
                    'strategy': strategy,
                    'command_queue': command_queue,
                    'mode':strategy_params['mode']
                })
                command_queues[currency] = command_queue
                strategy.start()
                save_strategy_params(strategies, account_id)  # 保存参数信息
            elif command == 'stop':
                currency,mode=input_ccy_mode()
                choice = input("Enter 1: stop the currency strategy \nEnter 2: stop the currency strategy after cycle\ninput:").strip().upper()
                for strategy_item in strategies:
                    if strategy_item['name'] == currency and strategy_item['mode']==mode :
                       if choice =='1':
                           action={'action':'close_position'}
                           strategy_item['command_queue'].put(action)
                           strategy_item['strategy'].join(timeout=10)  # 等待10秒
                           if strategy_item['strategy'].is_alive():
                               print(f"Thread for {currency} {mode} is still running after timeout")
                               strategy_item['command_queue'].put(action)
                               # 再次等待线程停止，重试等待5秒
                               strategy_item['strategy'].join(timeout=5)
                               if strategy_item['strategy'].is_alive():
                                   print(f"WARING:Thread for {currency} {mode} is still running after retry.")
                               else:
                                   strategies.remove(strategy_item)
                                   del command_queues[currency]
                                   save_strategy_params(strategies, account_id)
                                   print(f"Stopped strategy for {currency} {mode} after retry.")
                           else:
                               strategies.remove(strategy_item)
                               del command_queues[currency]
                               save_strategy_params(strategies, account_id)  # 保存参数信息
                               print(f"Stopped strategy for {currency} {mode}.")
                           break
                       elif choice=='2':
                           action={'action':'close_wait'}
                           strategy_item['command_queue'].put(action)
                           print(f"waiting for stopped strategy for {currency} {mode}.")
                           time.sleep(3)
                           save_strategy_params(strategies, account_id)
                       break
                else:
                    print(f"No strategy found for {currency}.")
            elif command == 'status':
                for strategy_item in strategies:
                    action={'action':'status'}
                    strategy_item['command_queue'].put(action)
            elif command == 'option_positions':
                # 查看期权持仓
                positions = get_option_positions()
                if positions:
                    print("\n当前期权持仓:")
                    for instId, size in positions.items():
                        print(f"{instId}: {size}")
                else:
                    print("当前无期权持仓")
            elif command == 'system_status':
                # 查看系统状态
                print(f"Active strategies: {len(strategies)}")
                print(f"Option worker alive: {option_worker.is_alive()}")
                print(f"Shutdown event set: {shutdown_event.is_set()}")
                # WebSocket有自动重连机制，不需要监控连接状态
    
    except Exception as e:
        logging.critical(f"Critical error in main loop: {e}")
        print(f"Critical error occurred: {e}")
    
    # 开始清理程序
    print("Stopping all strategy threads...")
    for strategy_item in strategies[:]:  # 使用切片创建副本以避免修改列表时的问题
        try:
            strategy_item['strategy'].stop()
            strategy_item['strategy'].join(timeout=10)
            if strategy_item['strategy'].is_alive():
                logging.critical(f"Strategy {strategy_item['name']} failed to stop gracefully")
            else:
                print(f"Strategy {strategy_item['name']} stopped successfully")
        except Exception as e:
            logging.critical(f"Error stopping strategy {strategy_item['name']}: {e}")
    
    # 2. 停止WebSocket订阅
    print("Stopping WebSocket subscriber...")
    try:
        if subscriber:
            subscriber.stop()
            time.sleep(2)  # 给WebSocket一些时间来清理
    except Exception as e:
        logging.critical(f"Error stopping WebSocket subscriber: {e}")
    
    # 3. 停止期权对冲工作线程
    print("Stopping option hedge worker...")
    try:
        option_worker.stop()
        option_worker.join(timeout=5)  # 设置超时防止程序卡住
        if option_worker.is_alive():
            logging.critical("Option hedge worker failed to stop gracefully")
        else:
            print("Option hedge worker stopped successfully")
    except Exception as e:
        logging.critical(f"Error stopping option hedge worker: {e}")
    
    # 4. 等待打印线程结束
    print("Waiting for printer thread to finish...")
    try:
        printer_thread.join(timeout=3)
        if printer_thread.is_alive():
            logging.critical("Printer thread failed to stop gracefully")
    except Exception as e:
        logging.critical(f"Error with printer thread: {e}")
    
   


apikey = "50fe3b78-1019-433d-9f64-675e47a7daaa" 
secretkey = "37C1783FB06567FE998CE1FC97FC242A"
passphrase ='Qxat240925.'
account_id ='test'
# 2. 配置 root logger
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filename=f"strategy_{account_id}.log",     # 所有 root logger 输出都写到这里
    filemode="a"            # 追加模式
)

flag = "0"  # 实盘:0 , 模拟盘:1
accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
tradeAPI = Trade.TradeAPI(apikey, secretkey, passphrase, False, flag)
publicDataAPI = PublicData.PublicAPI(flag=flag)
marketDataAPI =  MarketData.MarketAPI(flag=flag)


result = accountAPI.set_position_mode(posMode="long_short_mode")
subscriber = OKXWebSocketSubscriber(apikey, secretkey, passphrase)
subscriber.start()
time.sleep(5)

rebalance=0
global_lock = threading.Lock()
message_status=[]


# >>> MOD START: 关闭 keep-alive，避免阿里云 15-min idle-RST （兼容不同 SDK 版本）
def set_short_connection(api):
    sess = None
    if hasattr(api, '_session'):
        sess = api._session
    elif hasattr(api, 'session'):
        sess = api.session
    elif hasattr(api, '_client') and hasattr(api._client, 'session'):
        sess = api._client.session
    if sess:
        try:
            sess.headers.update({"Connection": "close"})
            sess.keep_alive = False
        except Exception:
            pass

# 对四个 OKX API 实例统一应用“短连接”策略
for api_instance in (accountAPI, tradeAPI, publicDataAPI, marketDataAPI):
    set_short_connection(api_instance)

if __name__ == "__main__":
    if not apikey or not secretkey or not passphrase:
            print("Error: API credentials not configured properly")
            exit(1)
        
    print("=== 交易系统启动 ===")
    print(f"账户ID: {account_id}")
    get_balance()
    print("可用命令:")
    print("- start: 启动新策略")
    print("- stop: 停止指定策略")
    print("- status: 查看所有策略状态")
    print("- option_positions: 查看期权持仓")
    print("- system_status: 查看系统运行状态")
    print("- exit: 正常退出程序")
    print("=" * 30)
        
    main()



"""
程序功能说明:

1. 马丁格尔交易策略系统
2. 支持多个币种同时运行
3. 支持做多和做空模式
4. 集成期权对冲功能
5. 自动保存和恢复策略状态

使用建议:
- 定期检查日志文件中的错误消息
- 确保网络连接稳定
- 根据市场情况调整策略参数

主要命令:
- start: 启动新策略
- stop: 停止指定策略  
- status: 查看策略状态
- option_positions: 查看期权持仓
- system_status: 查看系统状态
- exit: 退出程序
"""