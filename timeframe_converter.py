#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
时间周期转换工具
将数据库中的1分钟K线数据合成为4小时K线数据，用于回测
"""

import sqlite3
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

class TimeframeConverter:
    """时间周期转换器"""
    
    def __init__(self, db_path: str = "howtrader/database.db"):
        self.db_path = db_path
        self.conn = None
        
    def connect_db(self):
        """连接数据库"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            print(f"✅ 已连接数据库: {self.db_path}")
            return True
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            return False
    
    def close_db(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            print("🔒 数据库连接已关闭")
    
    def get_available_symbols(self):
        """获取可用的交易对和时间周期"""
        if not self.conn:
            self.connect_db()
            
        query = """
        SELECT symbol, interval, COUNT(*) as count, MIN(datetime) as start_time, MAX(datetime) as end_time
        FROM dbbardata 
        GROUP BY symbol, interval
        ORDER BY symbol, interval
        """
        
        df = pd.read_sql_query(query, self.conn)
        print("\n📊 可用数据概览:")
        print("-" * 80)
        for _, row in df.iterrows():
            print(f"📈 {row['symbol']} ({row['interval']}) - {row['count']:,} 根K线")
            print(f"   时间范围: {row['start_time']} ~ {row['end_time']}")
        print("-" * 80)
        
        return df
    
    def load_minute_data(self, symbol: str, exchange: str = "OKX"):
        """加载1分钟数据"""
        if not self.conn:
            self.connect_db()
            
        query = """
        SELECT datetime, open_price, high_price, low_price, close_price, volume
        FROM dbbardata 
        WHERE symbol = ? AND exchange = ? AND interval = '1m'
        ORDER BY datetime ASC
        """
        
        df = pd.read_sql_query(query, self.conn, params=[symbol, exchange])
        
        if df.empty:
            print(f"⚠️  未找到 {symbol} 的1分钟数据")
            return None
            
        # 转换时间格式
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        
        print(f"✅ 加载 {symbol} 1分钟数据: {len(df):,} 根K线")
        print(f"   时间范围: {df.index[0]} ~ {df.index[-1]}")
        
        return df
    
    def convert_to_4h(self, df: pd.DataFrame):
        """将1分钟数据转换为4小时数据"""
        if df is None or df.empty:
            return None
            
        print("🔄 开始转换为4小时K线...")
        
        # 使用pandas resample进行重采样
        df_4h = df.resample('4H', label='left', closed='left').agg({
            'open_price': 'first',
            'high_price': 'max', 
            'low_price': 'min',
            'close_price': 'last',
            'volume': 'sum'
        }).dropna()
        
        # 重命名列以匹配数据库格式
        df_4h.columns = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
        
        print(f"✅ 转换完成: {len(df_4h):,} 根4小时K线")
        print(f"   时间范围: {df_4h.index[0]} ~ {df_4h.index[-1]}")
        
        return df_4h
    
    def save_4h_data(self, symbol: str, df_4h: pd.DataFrame, exchange: str = "OKX"):
        """保存4小时数据到数据库"""
        if df_4h is None or df_4h.empty:
            print("❌ 没有数据可保存")
            return False
            
        print("💾 保存4小时数据到数据库...")
        
        # 准备插入数据
        insert_data = []
        for dt, row in df_4h.iterrows():
            # 确保dt是datetime对象并格式化
            if hasattr(dt, 'strftime'):
                dt_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                dt_str = str(dt)
            insert_data.append((
                symbol,
                exchange,
                dt_str,
                '4h',
                row['open_price'],
                row['high_price'], 
                row['low_price'],
                row['close_price'],
                row['volume'],
                0,  # open_interest
                0   # turnover
            ))
        
        try:
            # 使用INSERT OR REPLACE避免重复
            if self.conn is None:
                print("❌ 数据库连接为空")
                return False
                
            cursor = self.conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO dbbardata 
                (symbol, exchange, datetime, interval, open_price, high_price, 
                 low_price, close_price, volume, open_interest, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_data)
            
            self.conn.commit()
            print(f"✅ 成功保存 {len(insert_data):,} 根4小时K线")
            return True
            
        except Exception as e:
            print(f"❌ 保存失败: {e}")
            return False
    
    def convert_symbol_to_4h(self, symbol: str, exchange: str = "OKX"):
        """转换指定交易对为4小时数据"""
        print(f"\n🔄 开始处理 {symbol}...")
        
        # 1. 加载1分钟数据
        df_1m = self.load_minute_data(symbol, exchange)
        if df_1m is None:
            return False
            
        # 2. 转换为4小时
        df_4h = self.convert_to_4h(df_1m)
        if df_4h is None:
            return False
            
        # 3. 保存到数据库
        success = self.save_4h_data(symbol, df_4h, exchange)
        
        if success:
            print(f"🎉 {symbol} 4小时数据转换完成！")
        
        return success
    
    def batch_convert_all_1m_data(self):
        """批量转换所有1分钟数据为4小时"""
        print("🚀 批量转换所有1分钟数据为4小时K线")
        print("=" * 60)
        
        # 获取所有1分钟数据的交易对
        if not self.conn:
            self.connect_db()
            
        query = """
        SELECT DISTINCT symbol, exchange 
        FROM dbbardata 
        WHERE interval = '1m'
        ORDER BY symbol
        """
        
        df_symbols = pd.read_sql_query(query, self.conn)
        
        if df_symbols.empty:
            print("⚠️  未找到1分钟数据")
            return
            
        print(f"📊 找到 {len(df_symbols)} 个交易对的1分钟数据:")
        for _, row in df_symbols.iterrows():
            print(f"   • {row['symbol']} ({row['exchange']})")
        
        # 逐个转换
        success_count = 0
        for _, row in df_symbols.iterrows():
            if self.convert_symbol_to_4h(row['symbol'], row['exchange']):
                success_count += 1
                
        print(f"\n🎉 批量转换完成！成功: {success_count}/{len(df_symbols)}")

def main():
    """主函数"""
    print("⏰ HowTrader 时间周期转换工具")
    print("=" * 50)
    
    converter = TimeframeConverter()
    
    try:
        # 连接数据库
        if not converter.connect_db():
            return
            
        # 显示可用数据
        converter.get_available_symbols()
        
        # 批量转换所有1分钟数据
        converter.batch_convert_all_1m_data()
        
        # 最后再次显示数据概览
        print("\n" + "=" * 50)
        print("📊 转换后数据概览:")
        converter.get_available_symbols()
        
    finally:
        converter.close_db()

if __name__ == "__main__":
    main() 