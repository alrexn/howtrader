#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OKX智能历史数据下载器
- 自动下载主要币种永续合约完整历史数据
- 支持手动指定标的和时间范围下载
- 数据库查看和统计功能
- 数据连续性检查和自动补全
- 支持现货、永续合约、交割合约
"""

import time
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from threading import Thread
from typing import List, Dict, Optional, Tuple
from howtrader.event import EventEngine
from howtrader.trader.setting import SETTINGS
from howtrader.trader.engine import MainEngine
from howtrader.trader.database import BaseDatabase, get_database
from howtrader.trader.object import HistoryRequest, BarData, Exchange, Interval
from howtrader.gateway.okx import OkxGateway

# 配置日志
SETTINGS["log.active"] = True
SETTINGS["log.console"] = True
SETTINGS["log.file"] = True

# OKX API 配置
OKX_SETTING = {
    "key": "50fe3b78-1019-433d-9f64-675e47a7daaa",
    "secret": "37C1783FB06567FE998CE1FC97FC242A", 
    "passphrase": "Qxat240925.",
    "proxy_host": "",
    "proxy_port": 0,
    "server": "REAL"
}

# 主要币种上市时间配置（永续合约）
SYMBOL_LAUNCH_DATES = {
    "BTC-USDT-SWAP": "2020-03-13",  # BTC永续合约上市时间
    "ETH-USDT-SWAP": "2020-03-13",  # ETH永续合约上市时间
    "SOL-USDT-SWAP": "2021-09-09",  # SOL永续合约上市时间
    "PEPE-USDT-SWAP": "2023-05-10", # PEPE永续合约上市时间
}

class OkxSmartDataDownloader:
    """OKX智能数据下载器"""
    
    def __init__(self, db_path: str = "howtrader/database.db"):
        self.db_path = db_path
        self.database: BaseDatabase = get_database()
        self.gateway = None
        self.main_engine = None
        
    def connect(self):
        """连接OKX交易所"""
        print("🔗 初始化OKX连接...")
        
        try:
            event_engine = EventEngine()
            self.main_engine = MainEngine(event_engine)
            self.main_engine.add_gateway(OkxGateway)
            
            # 连接交易所
            self.main_engine.connect(OKX_SETTING, "OKX")
            time.sleep(5)  # 等待连接稳定
            
            # 获取网关
            self.gateway = self.main_engine.get_gateway("OKX")
            if self.gateway:
                print("✅ OKX连接成功")
                return True
            else:
                print("❌ OKX连接失败")
                return False
                
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return False
    
    def close(self):
        """关闭连接"""
        if self.main_engine:
            self.main_engine.close()
            print("🔄 连接已关闭")
    
    def get_database_info(self):
        """查看数据库行情数据情况"""
        print("\n📊 数据库行情数据概览")
        print("=" * 80)
        
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 查询所有交易对的数据统计
            query = """
            SELECT 
                symbol,
                exchange,
                interval,
                COUNT(*) as count,
                MIN(datetime) as start_time,
                MAX(datetime) as end_time,
                MIN(open_price) as min_price,
                MAX(high_price) as max_price
            FROM dbbardata 
            GROUP BY symbol, exchange, interval
            ORDER BY symbol, interval
            """
            
            df = pd.read_sql_query(query, conn)
            
            if df.empty:
                print("⚠️  数据库中暂无行情数据")
                return
            
            # 按交易对分组显示
            current_symbol = ""
            for _, row in df.iterrows():
                if row['symbol'] != current_symbol:
                    current_symbol = row['symbol']
                    print(f"\n📈 {current_symbol} ({row['exchange']})")
                    print("-" * 60)
                
                # 计算天数
                start_dt = pd.to_datetime(row['start_time'])
                end_dt = pd.to_datetime(row['end_time'])
                days = (end_dt - start_dt).days
                
                print(f"   {row['interval']:>4} | {row['count']:>8,} 根K线 | {days:>3}天 | {row['start_time']} ~ {row['end_time']}")
                print(f"        | 价格区间: ${row['min_price']:>8.2f} ~ ${row['max_price']:>8.2f}")
            
            # 总计统计
            total_bars = df['count'].sum()
            unique_symbols = df['symbol'].nunique()
            print(f"\n📊 总计: {unique_symbols} 个交易对, {total_bars:,} 根K线")
            
            # 显示最新的5条K线数据
            print(f"\n📋 最新5条K线数据示例:")
            latest_query = """
            SELECT symbol, datetime, open_price, high_price, low_price, close_price, volume
            FROM dbbardata 
            ORDER BY datetime DESC 
            LIMIT 5
            """
            latest_df = pd.read_sql_query(latest_query, conn)
            
            if not latest_df.empty:
                print("-" * 100)
                for _, row in latest_df.iterrows():
                    print(f"{row['symbol']:>15} | {row['datetime']} | "
                          f"开:{row['open_price']:>8.2f} | 高:{row['high_price']:>8.2f} | "
                          f"低:{row['low_price']:>8.2f} | 收:{row['close_price']:>8.2f} | "
                          f"量:{row['volume']:>10,.0f}")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ 查询数据库失败: {e}")
    
    def check_data_continuity(self, symbol: str, exchange: str = "OKX", interval: str = "1m") -> List[Tuple[str, str]]:
        """检查数据连续性，返回缺失的时间段"""
        print(f"\n🔍 检查 {symbol} {interval} 数据连续性...")
        
        gaps = []
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 获取该交易对的所有数据时间点
            query = """
            SELECT datetime 
            FROM dbbardata 
            WHERE symbol = ? AND exchange = ? AND interval = ?
            ORDER BY datetime ASC
            """
            
            df = pd.read_sql_query(query, conn, params=[symbol, exchange, interval])
            conn.close()
            
            if df.empty:
                print(f"⚠️  未找到 {symbol} 的数据")
                return gaps
            
            # 转换为datetime格式
            df['datetime'] = pd.to_datetime(df['datetime'])
            
            # 根据时间周期确定间隔
            if interval == "1m":
                freq = "1min"
                tolerance = timedelta(minutes=5)  # 允许5分钟误差
            elif interval == "1h":
                freq = "1H"
                tolerance = timedelta(hours=2)
            elif interval == "1d":
                freq = "1D"
                tolerance = timedelta(days=2)
            else:
                print(f"⚠️  不支持的时间周期: {interval}")
                return gaps
            
            # 生成完整的时间序列
            start_time = df['datetime'].min()
            end_time = df['datetime'].max()
            full_range = pd.date_range(start=start_time, end=end_time, freq=freq)
            
            # 找出缺失的时间点
            existing_times = set(df['datetime'])
            missing_times = []
            
            for dt in full_range:
                if dt not in existing_times:
                    missing_times.append(dt)
            
            # 将连续的缺失时间合并为时间段
            if missing_times:
                missing_times.sort()
                gap_start = missing_times[0]
                gap_end = missing_times[0]
                
                for i in range(1, len(missing_times)):
                    current_time = missing_times[i]
                    expected_next = gap_end + pd.Timedelta(freq)
                    
                    if abs((current_time - expected_next).total_seconds()) <= tolerance.total_seconds():
                        # 连续缺失
                        gap_end = current_time
                    else:
                        # 不连续，保存当前gap并开始新的gap
                        gaps.append((gap_start.strftime('%Y-%m-%d %H:%M:%S'), gap_end.strftime('%Y-%m-%d %H:%M:%S')))
                        gap_start = current_time
                        gap_end = current_time
                
                # 添加最后一个gap
                gaps.append((gap_start.strftime('%Y-%m-%d %H:%M:%S'), gap_end.strftime('%Y-%m-%d %H:%M:%S')))
            
            # 显示结果
            total_expected = len(full_range)
            total_existing = len(df)
            completeness = (total_existing / total_expected) * 100
            
            print(f"📊 数据完整度: {completeness:.1f}% ({total_existing:,}/{total_expected:,})")
            
            if gaps:
                print(f"⚠️  发现 {len(gaps)} 个数据缺口，正在自动补全...")
                filled_count = 0
                for i, (start, end) in enumerate(gaps, 1):
                    print(f"   {i}. {start} ~ {end} -> 开始补全...")
                    # 自动补全缺口数据
                    start_date = start[:10]
                    end_date = end[:10]
                    
                    # 转换字符串interval为Interval枚举
                    interval_map = {
                        "1m": Interval.MINUTE,
                        "1h": Interval.HOUR, 
                        "1d": Interval.DAILY
                    }
                    interval_obj = interval_map.get(interval, Interval.MINUTE)
                    
                    if self.download_data_with_retry(symbol, start_date, end_date, interval_obj):
                        print(f"      ✅ 补全成功")
                        filled_count += 1
                    else:
                        print(f"      ❌ 补全失败")
                print(f"🎉 补全完成！成功: {filled_count}/{len(gaps)}")
            else:
                print("✅ 数据连续性良好，无缺口")
            
            return gaps
            
        except Exception as e:
            print(f"❌ 检查连续性失败: {e}")
            return gaps
    
    def download_data_with_retry(
        self, 
        symbol: str, 
        start_date: str, 
        end_date: str, 
        interval: Interval = Interval.MINUTE,
        max_retries: int = 3
    ) -> bool:
        """下载数据（带重试机制）"""
        if not self.gateway:
            print("❌ 请先连接交易所")
            return False
        
        # 识别产品类型
        if "-SWAP" in symbol:
            product_type = "永续合约"
        elif symbol.count("-") >= 2 and "-SWAP" not in symbol:
            product_type = "交割合约"
        else:
            product_type = "现货"
        
        print(f"\n📥 下载 {product_type} {symbol} {interval.value} 数据")
        print(f"📅 时间范围: {start_date} ~ {end_date}")
        
        for attempt in range(max_retries):
            try:
                # 转换日期格式
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                
                # 创建历史数据请求
                req = HistoryRequest(
                    symbol=symbol,
                    exchange=Exchange.OKX,
                    interval=interval,
                    start=start_dt,
                    end=end_dt
                )
                
                # 下载数据
                bars: List[BarData] = self.gateway.query_history(req)
                
                if bars:
                     # 检查连续性
                    self.check_data_continuity(symbol, "OKX", interval.value)
                    # 保存到数据库
                    self.database.save_bar_data(bars)
                    print(f"✅ 成功下载 {len(bars):,} 根K线")
                    print(f"📊 时间范围: {bars[0].datetime} ~ {bars[-1].datetime}")
                    return True
                else:
                    print(f"⚠️  第{attempt+1}次尝试未获取到数据")
                    
            except Exception as e:
                print(f"❌ 第{attempt+1}次下载失败: {e}")
                
            if attempt < max_retries - 1:
                print(f"🔄 等待5秒后重试...")
                time.sleep(5)
        
        print(f"❌ {max_retries}次尝试均失败")
        return False
    
    def first_time_download_major_swaps(self):
        """首次下载主要币种永续合约完整历史数据"""
        print("\n🚀 首次下载主要币种永续合约完整历史数据")
        print("=" * 70)
        print("📋 目标币种: BTC, ETH, SOL, PEPE")
        print("⏰ 时间周期: 1分钟K线")
        print("📅 时间范围: 上市日期 ~ 最新日期")
        print("⚠️  注意：这将下载完整历史数据，可能需要较长时间")
        
        confirm = input("\n确认开始首次完整下载？(y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 已取消下载")
            return
        
        today = datetime.now().strftime('%Y-%m-%d')
        success_count = 0
        
        for symbol, launch_date in SYMBOL_LAUNCH_DATES.items():
            print(f"\n{'='*50}")
            print(f"🎯 处理 {symbol}")
            print(f"📅 上市时间: {launch_date}")
            print(f"📅 目标范围: {launch_date} ~ {today}")
            
            try:
                # 直接下载完整历史数据
                if self.download_data_with_retry(symbol, launch_date, today, Interval.MINUTE):
                    success_count += 1
                        
            except Exception as e:
                print(f"❌ 处理{symbol}失败: {e}")
                
            time.sleep(1)  # 避免频率限制
        
        print(f"\n🎉 首次下载完成！成功: {success_count}/{len(SYMBOL_LAUNCH_DATES)}")
        
        # 最后显示数据库概览
        self.get_database_info()
    
    def auto_update_major_swaps(self):
        """自动更新主要币种永续合约数据（增量更新）"""
        print("\n🔄 自动增量更新主要币种永续合约数据")
        print("=" * 70)
        print("📋 目标币种: BTC, ETH, SOL, PEPE")
        print("⏰ 时间周期: 1分钟K线")
        print("📅 功能：检查数据库最后日期到最新日期的空白数据")
        
        today = datetime.now().strftime('%Y-%m-%d')
        success_count = 0
        
        for symbol, launch_date in SYMBOL_LAUNCH_DATES.items():
            print(f"\n{'='*50}")
            print(f"🎯 处理 {symbol}")
            
            # 检查数据库中是否已有数据
            try:
                conn = sqlite3.connect(self.db_path)
                query = """
                SELECT MIN(datetime) as min_date, MAX(datetime) as max_date, COUNT(*) as count
                FROM dbbardata 
                WHERE symbol = ? AND exchange = 'OKX' AND interval = '1m'
                """
                result = pd.read_sql_query(query, conn, params=[symbol])
                conn.close()
                
                if not result.empty and result.iloc[0]['count'] > 0:
                    existing_start = result.iloc[0]['min_date']
                    existing_end = result.iloc[0]['max_date']
                    existing_count = result.iloc[0]['count']
                    
                    print(f"💾 已有数据: {existing_count:,} 根K线")
                    print(f"📊 数据范围: {existing_start} ~ {existing_end}")
                    
                    # 计算需要补充的时间段
                    existing_end_dt = datetime.strptime(existing_end[:10], '%Y-%m-%d')
                    today_dt = datetime.strptime(today, '%Y-%m-%d')
                    
                    if existing_end_dt < today_dt - timedelta(days=1):
                        # 需要更新到最新
                        update_start = (existing_end_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                        print(f"🔄 需要更新: {update_start} ~ {today}")
                        
                        if self.download_data_with_retry(symbol, update_start, today, Interval.MINUTE):
                            success_count += 1
                    else:
                        print("✅ 数据已是最新")
                        success_count += 1
                else:
                    # 数据库中没有数据
                    print("⚠️  数据库中没有此币种数据")
                    print("💡 建议先使用'首次下载'功能获取完整历史数据")
                        
            except Exception as e:
                print(f"❌ 处理{symbol}失败: {e}")
                
            time.sleep(3)  # 避免频率限制
        
        print(f"\n🎉 增量更新完成！成功: {success_count}/{len(SYMBOL_LAUNCH_DATES)}")
        
        # 最后显示数据库概览
        self.get_database_info()
    
    def manual_download(self):
        """手动指定下载"""
        print("\n📋 手动指定行情数据下载")
        print("=" * 50)
        
        # 输入交易对
        print("\n📈 请输入交易对信息:")
        print("现货示例: BTC-USDT, ETH-USDT")
        print("永续合约示例: BTC-USDT-SWAP, ETH-USDT-SWAP") 
        print("交割合约示例: BTC-USDT-241227, ETH-USDT-250328")
        
        symbol = input("交易对: ").strip().upper()
        if not symbol:
            print("❌ 交易对不能为空")
            return
        
        # 输入时间范围
        print("\n📅 请输入时间范围 (格式: YYYY-MM-DD):")
        start_date = input("起始日期: ").strip()
        end_date = input("终止日期: ").strip()
        
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
            datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            print("❌ 日期格式错误，请使用 YYYY-MM-DD 格式")
            return
        
        # 选择时间周期
        print("\n⏰ 请选择时间周期:")
        print("1. 1分钟")
        print("2. 1小时") 
        print("3. 1日")
        
        interval_choice = input("选择 (1-3): ").strip()
        interval_map = {
            "1": Interval.MINUTE,
            "2": Interval.HOUR,
            "3": Interval.DAILY
        }
        
        if interval_choice not in interval_map:
            print("❌ 无效选择")
            return
            
        interval = interval_map[interval_choice]
        
        # 确认下载
        print(f"\n📋 下载确认:")
        print(f"交易对: {symbol}")
        print(f"时间范围: {start_date} ~ {end_date}")
        print(f"时间周期: {interval.value}")
        
        confirm = input("\n确认下载? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 已取消")
            return
        
        # 执行下载
        success = self.download_data_with_retry(symbol, start_date, end_date, interval)
        
        if success:
            print("✅ 下载完成")
        else:
            print("❌ 下载失败")
    
    def fill_data_gaps(self, symbol: str, exchange: str = "OKX", interval: str = "1m"):
        """补全数据缺口"""
        print(f"\n🔧 补全 {symbol} {interval} 数据缺口")
        
        gaps = self.check_data_continuity(symbol, exchange, interval)
        
        if not gaps:
            print("✅ 无需补全")
            return
        
        # 转换interval格式
        interval_map = {
            "1m": Interval.MINUTE,
            "1h": Interval.HOUR, 
            "1d": Interval.DAILY
        }
        
        if interval not in interval_map:
            print(f"❌ 不支持的时间周期: {interval}")
            return
            
        interval_obj = interval_map[interval]
        
        print(f"🔧 开始补全 {len(gaps)} 个缺口...")
        
        success_count = 0
        for i, (start_time, end_time) in enumerate(gaps, 1):
            print(f"\n补全缺口 {i}/{len(gaps)}: {start_time} ~ {end_time}")
            
            # 转换为日期格式
            start_date = start_time[:10]
            end_date = end_time[:10]
            
            if self.download_data_with_retry(symbol, start_date, end_date, interval_obj):
                success_count += 1
            
            time.sleep(2)  # 避免频率限制
        
        print(f"\n🎉 补全完成！成功: {success_count}/{len(gaps)}")
    
    def export_to_csv(self):
        """导出数据库表到CSV文件"""
        print("\n📤 导出数据库表到CSV文件")
        print("=" * 50)
        
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 获取所有可用的交易对和时间周期
            query = """
            SELECT DISTINCT symbol, exchange, interval, COUNT(*) as count
            FROM dbbardata 
            GROUP BY symbol, exchange, interval
            ORDER BY symbol, interval
            """
            
            df_tables = pd.read_sql_query(query, conn)
            
            if df_tables.empty:
                print("⚠️  数据库中没有数据可导出")
                conn.close()
                return
            
            print("📋 可导出的数据表:")
            for i, (_, row) in enumerate(df_tables.iterrows(), 1):
                print(f"   {i:>2}. {row['symbol']} - {row['interval']} ({row['count']:,} 根K线)")
            
            # 用户选择
            choice = input(f"\n请选择要导出的表 (1-{len(df_tables)}): ").strip()
            
            try:
                choice_idx = int(choice) - 1
                if choice_idx < 0 or choice_idx >= len(df_tables):
                    print("❌ 无效选择")
                    conn.close()
                    return
                
                selected = df_tables.iloc[choice_idx]
                symbol = selected['symbol']
                exchange = selected['exchange']
                interval = selected['interval']
                
                print(f"\n📥 导出 {symbol} - {interval} 数据...")
                
                # 查询数据
                export_query = """
                SELECT datetime, open_price, high_price, low_price, close_price, volume
                FROM dbbardata 
                WHERE symbol = ? AND exchange = ? AND interval = ?
                ORDER BY datetime ASC
                """
                
                df_export = pd.read_sql_query(export_query, conn, params=[symbol, exchange, interval])
                
                # 生成文件名
                symbol_clean = symbol.replace("-", "_")
                filename = f"{symbol_clean}_{interval}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                
                # 导出CSV
                df_export.to_csv(filename, index=False)
                
                print(f"✅ 成功导出 {len(df_export):,} 条数据到文件: {filename}")
                print(f"📊 时间范围: {df_export['datetime'].min()} ~ {df_export['datetime'].max()}")
                
            except ValueError:
                print("❌ 请输入有效的数字")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ 导出失败: {e}")
    
    def delete_table_data(self):
        """删除数据库中指定表的数据"""
        print("\n🗑️  删除数据库表数据")
        print("=" * 50)
        print("⚠️  警告：此操作不可恢复！")
        
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 获取所有可用的交易对和时间周期
            query = """
            SELECT DISTINCT symbol, exchange, interval, COUNT(*) as count
            FROM dbbardata 
            GROUP BY symbol, exchange, interval
            ORDER BY symbol, interval
            """
            
            df_tables = pd.read_sql_query(query, conn)
            
            if df_tables.empty:
                print("⚠️  数据库中没有数据可删除")
                conn.close()
                return
            
            print("📋 可删除的数据表:")
            for i, (_, row) in enumerate(df_tables.iterrows(), 1):
                print(f"   {i:>2}. {row['symbol']} - {row['interval']} ({row['count']:,} 根K线)")
            
            # 用户选择
            choice = input(f"\n请选择要删除的表 (1-{len(df_tables)}): ").strip()
            
            try:
                choice_idx = int(choice) - 1
                if choice_idx < 0 or choice_idx >= len(df_tables):
                    print("❌ 无效选择")
                    conn.close()
                    return
                
                selected = df_tables.iloc[choice_idx]
                symbol = selected['symbol']
                exchange = selected['exchange']
                interval = selected['interval']
                count = selected['count']
                
                # 二次确认
                confirm = input(f"\n⚠️  确认删除 {symbol} - {interval} 的 {count:,} 条数据？(输入 'DELETE' 确认): ").strip()
                
                if confirm != 'DELETE':
                    print("❌ 已取消删除操作")
                    conn.close()
                    return
                
                print(f"\n🗑️  正在删除 {symbol} - {interval} 数据...")
                
                # 执行删除
                delete_query = """
                DELETE FROM dbbardata 
                WHERE symbol = ? AND exchange = ? AND interval = ?
                """
                
                cursor = conn.cursor()
                cursor.execute(delete_query, (symbol, exchange, interval))
                deleted_count = cursor.rowcount
                conn.commit()
                
                print(f"✅ 成功删除 {deleted_count:,} 条数据")
                
            except ValueError:
                print("❌ 请输入有效的数字")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ 删除失败: {e}")

def main():
    """主函数"""
    print("🚀 OKX智能历史数据下载器")
    print("=" * 60)
    
    downloader = OkxSmartDataDownloader()
    
    while True:
        print("\n📋 请选择功能:")
        print("1. 首次下载主要币种永续合约 (BTC/ETH/SOL/PEPE) - 完整历史数据")
        print("2. 增量更新主要币种永续合约 (BTC/ETH/SOL/PEPE) - 只更新最新数据")
        print("3. 手动指定下载")
        print("4. 查看数据库概览")
        print("5. 数据连续性检查")
        print("6. 补全数据缺口")
        print("7. 导出数据库表到CSV文件")
        print("8. 删除数据库表数据")
        print("0. 退出")
        
        choice = input("\n输入选择 (0-8): ").strip()
        
        if choice == "1":
            if downloader.connect():
                downloader.first_time_download_major_swaps()
                downloader.close()
            
        elif choice == "2":
            if downloader.connect():
                downloader.auto_update_major_swaps()
                downloader.close()
        
        elif choice == "3":
            if downloader.connect():
                downloader.manual_download()
                downloader.close()
        
        elif choice == "4":
            downloader.get_database_info()
        
        elif choice == "5":
            downloader.get_database_info()
            symbol = input("\n请输入要检查的交易对: ").strip().upper()
            if symbol:
                downloader.check_data_continuity(symbol)
        
        elif choice == "6":
            downloader.get_database_info()
            symbol = input("\n请输入要补全的交易对: ").strip().upper()
            if symbol:
                if downloader.connect():
                    downloader.fill_data_gaps(symbol)
                    downloader.close()
        
        elif choice == "7":
            downloader.export_to_csv()
        
        elif choice == "8":
            downloader.delete_table_data()
        
        elif choice == "0":
            print("👋 再见!")
            break
        
        else:
            print("❌ 无效选择，请重新输入")

if __name__ == "__main__":
    main() 