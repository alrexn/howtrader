"""
自动数据库检查器
- 自动查看数据库状态
- 检查数据连续性
- 分析数据质量
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from howtrader.trader.database import get_database

class AutoDatabaseChecker:
    """自动数据库检查器"""
    
    def __init__(self):
        self.db_path = Path("howtrader/database.db")
        self.database = get_database()
        
    def check_all(self):
        """执行完整的数据库检查"""
        print("🔍 HowTrader 数据库自动检查")
        print("=" * 60)
        
        if not self.check_database_exists():
            return
            
        # 1. 基本信息
        self.show_basic_info()
        
        # 2. 数据汇总
        self.show_data_summary()
        
        # 3. 数据质量检查
        self.check_data_quality()
        
        # 4. 时间周期分析
        self.analyze_timeframes()
        
    def check_database_exists(self):
        """检查数据库文件"""
        if self.db_path.exists():
            size_mb = self.db_path.stat().st_size / (1024 * 1024)
            print(f"✅ 数据库文件: {self.db_path}")
            print(f"📁 文件大小: {size_mb:.1f} MB")
            return True
        else:
            print(f"❌ 数据库文件不存在: {self.db_path}")
            return False
    
    def show_basic_info(self):
        """显示基本信息"""
        conn = sqlite3.connect(self.db_path)
        
        print(f"\n📊 数据库基本信息:")
        print("-" * 40)
        
        # 查询总记录数
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dbbardata")
        total_bars = cursor.fetchone()[0]
        print(f"📈 总K线数量: {total_bars:,}")
        
        # 查询交易对数量
        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM dbbardata")
        symbol_count = cursor.fetchone()[0]
        print(f"💰 交易对数量: {symbol_count}")
        
        # 查询时间跨度
        cursor.execute("SELECT MIN(datetime), MAX(datetime) FROM dbbardata")
        min_time, max_time = cursor.fetchone()
        if min_time and max_time:
            print(f"📅 时间跨度: {min_time} 到 {max_time}")
            
        conn.close()
    
    def show_data_summary(self):
        """显示数据汇总"""
        conn = sqlite3.connect(self.db_path)
        
        print(f"\n📈 各交易对数据汇总:")
        print("-" * 50)
        
        query = """
        SELECT symbol, interval, 
               COUNT(*) as count,
               MIN(datetime) as start_time,
               MAX(datetime) as end_time
        FROM dbbardata 
        GROUP BY symbol, interval
        ORDER BY symbol, interval
        """
        
        df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("❌ 没有找到K线数据")
        else:
            for _, row in df.iterrows():
                print(f"📊 {row['symbol']} ({row['interval']})")
                print(f"   数据量: {row['count']:,} 根K线")
                print(f"   范围: {row['start_time']} ~ {row['end_time']}")
                print()
        
        conn.close()
        
    def check_data_quality(self):
        """检查数据质量和连续性"""
        conn = sqlite3.connect(self.db_path)
        
        print(f"🔍 数据质量检查:")
        print("-" * 30)
        
        # 检查各个交易对的数据连续性
        query = """
        SELECT symbol, interval, COUNT(*) as count,
               MIN(datetime) as start_time,
               MAX(datetime) as end_time
        FROM dbbardata 
        GROUP BY symbol, interval
        """
        
        df = pd.read_sql_query(query, conn)
        
        for _, row in df.iterrows():
            symbol = row['symbol']
            interval = row['interval']
            count = row['count']
            start_time = pd.to_datetime(row['start_time'])
            end_time = pd.to_datetime(row['end_time'])
            
            # 计算期望的K线数量
            if interval == '1m':
                expected_minutes = (end_time - start_time).total_seconds() / 60
                expected_count = int(expected_minutes)
                completeness = (count / expected_count) * 100 if expected_count > 0 else 0
            elif interval == '1H':
                expected_hours = (end_time - start_time).total_seconds() / 3600
                expected_count = int(expected_hours)
                completeness = (count / expected_count) * 100 if expected_count > 0 else 0
            elif interval == '1D':
                expected_days = (end_time - start_time).days + 1
                expected_count = expected_days
                completeness = (count / expected_count) * 100 if expected_count > 0 else 0
            else:
                completeness = 100  # 其他周期暂时认为完整
                
            status = "✅" if completeness > 95 else "⚠️" if completeness > 80 else "❌"
            print(f"{status} {symbol} {interval}: {completeness:.1f}% 完整度 ({count:,}/{expected_count:,})")
        
        conn.close()
        
    def analyze_timeframes(self):
        """分析时间周期情况"""
        conn = sqlite3.connect(self.db_path)
        
        print(f"\n⏰ 时间周期分析:")
        print("-" * 30)
        
        # 统计各时间周期数据
        query = """
        SELECT interval, 
               COUNT(DISTINCT symbol) as symbol_count,
               COUNT(*) as total_bars
        FROM dbbardata 
        GROUP BY interval
        ORDER BY interval
        """
        
        df = pd.read_sql_query(query, conn)
        
        for _, row in df.iterrows():
            print(f"📊 {row['interval']}: {row['symbol_count']} 个交易对, {row['total_bars']:,} 根K线")
        
        # 检查是否有1分钟数据可用于合成4小时
        minute_data = df[df['interval'] == '1m']
        hour4_data = df[df['interval'] == '4H']
        
        print(f"\n🔄 4小时K线合成分析:")
        if not minute_data.empty:
            print(f"✅ 发现1分钟数据: {minute_data.iloc[0]['total_bars']:,} 根K线")
            print(f"💡 可以合成4小时K线用于回测")
        else:
            print(f"❌ 没有1分钟数据，无法合成4小时K线")
            
        if not hour4_data.empty:
            print(f"✅ 已有4小时数据: {hour4_data.iloc[0]['total_bars']:,} 根K线")
        else:
            print(f"⚠️  暂无4小时K线数据")
            
        conn.close()
    
    def check_specific_symbol(self, symbol="BTC-USDT"):
        """检查特定交易对的详细信息"""
        conn = sqlite3.connect(self.db_path)
        
        print(f"\n🔍 {symbol} 详细分析:")
        print("-" * 40)
        
        query = """
        SELECT interval, COUNT(*) as count,
               MIN(datetime) as start_time,
               MAX(datetime) as end_time
        FROM dbbardata 
        WHERE symbol = ?
        GROUP BY interval
        ORDER BY interval
        """
        
        df = pd.read_sql_query(query, conn, params=[symbol])
        
        if df.empty:
            print(f"❌ 没有找到 {symbol} 的数据")
        else:
            for _, row in df.iterrows():
                print(f"📊 {row['interval']}: {row['count']:,} 根K线")
                print(f"   时间范围: {row['start_time']} ~ {row['end_time']}")
                
                # 显示最近几根K线
                recent_query = """
                SELECT datetime, open_price, high_price, low_price, close_price, volume
                FROM dbbardata 
                WHERE symbol = ? AND interval = ?
                ORDER BY datetime DESC 
                LIMIT 3
                """
                recent_df = pd.read_sql_query(recent_query, conn, params=[symbol, row['interval']])
                print(f"   最近3根K线:")
                for _, recent_row in recent_df.iterrows():
                    print(f"     {recent_row['datetime']}: O={recent_row['open_price']} H={recent_row['high_price']} L={recent_row['low_price']} C={recent_row['close_price']}")
                print()
        
        conn.close()

def main():
    """主函数"""
    checker = AutoDatabaseChecker()
    
    # 执行完整检查
    checker.check_all()
    
    # 检查BTC详细信息
    checker.check_specific_symbol("BTC-USDT")
    
    print("\n" + "="*60)
    print("🎉 数据库检查完成！")
    print("\n💡 下一步建议:")
    print("1. 如果数据不连续，可以重新下载")
    print("2. 如果只有1分钟数据，可以用BarGenerator合成4小时")
    print("3. 如果要回测4小时策略，确保有足够的4小时数据")

if __name__ == "__main__":
    main() 