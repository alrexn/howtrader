#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
交互式管理脚本 (Interactive Manager)
================================

统一管理所有交易账户的容器和策略配置

功能：
1. 🐳 容器管理 (启动/停止/重启)
2. ⚙️ 策略配置管理 (统一配置，批量更新)
3. 📊 实时监控 (所有账户状态)
4. 🎮 交互式控制 (命令行界面)
5. 🔧 配置热更新 (无需重启容器)

使用方式：
python interactive_manager.py
"""

import os
import sys
import time
import yaml
import json
import requests
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
LOGS_DIR = PROJECT_ROOT / "logs"

class ConfigManager:
    """配置管理器 - 统一管理全局策略配置和账户配置"""
    
    def __init__(self):
        self.global_strategy_file = CONFIG_DIR / "global_strategy.yaml"
        self.accounts_file = CONFIG_DIR / "accounts.yaml"
        
        # 加载配置
        self.global_config = self.load_global_config()
        self.accounts_config = self.load_accounts_config()
    
    def load_global_config(self) -> Dict:
        """加载全局策略配置"""
        try:
            with open(self.global_strategy_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"❌ 未找到全局配置文件: {self.global_strategy_file}")
            return {}
        except Exception as e:
            print(f"❌ 加载全局配置失败: {e}")
            return {}
    
    def load_accounts_config(self) -> Dict:
        """加载账户配置"""
        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"❌ 未找到账户配置文件: {self.accounts_file}")
            return {}
        except Exception as e:
            print(f"❌ 加载账户配置失败: {e}")
            return {}
    
    def save_global_config(self) -> bool:
        """保存全局策略配置"""
        try:
            # 更新时间戳
            self.global_config['strategy_config']['last_updated'] = datetime.now().isoformat()
            
            with open(self.global_strategy_file, 'w', encoding='utf-8') as f:
                yaml.dump(self.global_config, f, default_flow_style=False, allow_unicode=True)
            
            print(f"✅ 全局配置已保存: {self.global_strategy_file}")
            return True
        except Exception as e:
            print(f"❌ 保存全局配置失败: {e}")
            return False
    
    def get_account_config(self, account_id: str) -> Optional[Dict]:
        """获取指定账户的完整配置 (合并全局配置和账户特定配置)"""
        accounts = self.accounts_config.get('accounts', {})
        
        # 查找账户
        account_config = None
        for acc_key, acc_data in accounts.items():
            if acc_data.get('account_id') == account_id:
                account_config = acc_data.copy()
                break
        
        if not account_config:
            return None
        
        # 合并全局策略配置
        merged_config = {
            'account': account_config,
            'strategy': self.global_config.get('strategy_config', {}),
        }
        
        return merged_config
    
    def get_all_accounts(self) -> List[str]:
        """获取所有账户ID列表"""
        accounts = self.accounts_config.get('accounts', {})
        return [acc_data.get('account_id') for acc_data in accounts.values() 
                if acc_data.get('account_id')]
    
    def update_strategy_config(self, symbol: str, mode: str, config: Dict) -> bool:
        """更新策略配置"""
        try:
            if symbol.upper() in self.global_config['strategy_config']['martin_defaults']:
                self.global_config['strategy_config']['martin_defaults'][symbol.upper()][mode] = config
                return self.save_global_config()
            else:
                print(f"❌ 不支持的交易对: {symbol}")
                return False
        except Exception as e:
            print(f"❌ 更新策略配置失败: {e}")
            return False
    
    def toggle_strategy(self, symbol: str, mode: str, enabled: bool) -> bool:
        """启用/禁用策略"""
        try:
            config_path = ['strategy_config', 'martin_defaults', symbol.upper(), mode, 'enabled']
            
            # 设置enabled状态
            current = self.global_config
            for key in config_path[:-1]:
                current = current[key]
            current[config_path[-1]] = enabled
            
            return self.save_global_config()
        except Exception as e:
            print(f"❌ 切换策略状态失败: {e}")
            return False


class ContainerController:
    """容器控制器 - 管理Docker容器"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.compose_file = PROJECT_ROOT / "docker-compose.yml"
    
    def get_container_status(self, account_id: str) -> Dict:
        """获取容器状态"""
        container_name = f"howtrader-{account_id.lower().replace('_', '-')}"
        
        try:
            # 检查容器是否存在和运行状态
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
                capture_output=True, text=True
            )
            
            if result.returncode == 0 and len(result.stdout.strip().split('\n')) > 1:
                lines = result.stdout.strip().split('\n')[1:]  # 跳过表头
                for line in lines:
                    if container_name in line:
                        parts = line.split('\t')
                        return {
                            'name': parts[0],
                            'status': parts[1],
                            'ports': parts[2] if len(parts) > 2 else '',
                            'running': 'Up' in parts[1]
                        }
            
            return {
                'name': container_name,
                'status': 'Not Found',
                'ports': '',
                'running': False
            }
            
        except Exception as e:
            return {
                'name': container_name,
                'status': f'Error: {e}',
                'ports': '',
                'running': False
            }
    
    def start_container(self, account_id: str) -> bool:
        """启动指定账户的容器"""
        service_name = f"account-{account_id.lower().replace('_', '-')}"
        
        try:
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "up", "-d", service_name],
                capture_output=True, text=True, cwd=PROJECT_ROOT
            )
            
            if result.returncode == 0:
                print(f"✅ 容器启动成功: {account_id}")
                return True
            else:
                print(f"❌ 容器启动失败: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ 启动容器异常: {e}")
            return False
    
    def stop_container(self, account_id: str) -> bool:
        """停止指定账户的容器"""
        service_name = f"account-{account_id.lower().replace('_', '-')}"
        
        try:
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "stop", service_name],
                capture_output=True, text=True, cwd=PROJECT_ROOT
            )
            
            if result.returncode == 0:
                print(f"✅ 容器停止成功: {account_id}")
                return True
            else:
                print(f"❌ 容器停止失败: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ 停止容器异常: {e}")
            return False
    
    def restart_container(self, account_id: str) -> bool:
        """重启指定账户的容器"""
        service_name = f"account-{account_id.lower().replace('_', '-')}"
        
        try:
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "restart", service_name],
                capture_output=True, text=True, cwd=PROJECT_ROOT
            )
            
            if result.returncode == 0:
                print(f"✅ 容器重启成功: {account_id}")
                return True
            else:
                print(f"❌ 容器重启失败: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ 重启容器异常: {e}")
            return False
    
    def get_container_logs(self, account_id: str, lines: int = 50) -> str:
        """获取容器日志"""
        service_name = f"account-{account_id.lower().replace('_', '-')}"
        
        try:
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "logs", "--tail", str(lines), service_name],
                capture_output=True, text=True, cwd=PROJECT_ROOT
            )
            
            return result.stdout if result.returncode == 0 else f"获取日志失败: {result.stderr}"
            
        except Exception as e:
            return f"获取日志异常: {e}"


class AccountMonitor:
    """账户监控器 - 监控账户执行器状态"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
    
    def get_account_status(self, account_id: str) -> Dict:
        """获取账户执行器状态"""
        account_config = self.config_manager.get_account_config(account_id)
        if not account_config:
            return {'error': f'账户配置不存在: {account_id}'}
        
        port = account_config['account'].get('container_port', 9001)
        
        try:
            # 调用账户执行器的API接口获取状态
            response = requests.get(f"http://localhost:{port}/status", timeout=5)
            if response.status_code == 200:
                return response.json()
            else:
                return {'error': f'API调用失败: {response.status_code}'}
                
        except requests.exceptions.RequestException as e:
            return {'error': f'连接失败: {e}'}
    
    def send_command(self, account_id: str, command: str, params: Dict = None) -> Dict:
        """向账户执行器发送命令"""
        account_config = self.config_manager.get_account_config(account_id)
        if not account_config:
            return {'error': f'账户配置不存在: {account_id}'}
        
        port = account_config['account'].get('container_port', 9001)
        
        try:
            payload = {'command': command}
            if params:
                payload.update(params)
            
            response = requests.post(f"http://localhost:{port}/command", 
                                   json=payload, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                return {'error': f'命令执行失败: {response.status_code}'}
                
        except requests.exceptions.RequestException as e:
            return {'error': f'发送命令失败: {e}'}


class InteractiveManager:
    """交互式管理器 - 主控制台"""
    
    def __init__(self):
        self.config_manager = ConfigManager()
        self.container_controller = ContainerController(self.config_manager)
        self.account_monitor = AccountMonitor(self.config_manager)
        
        print("🚀 HowTrader 交互式管理控制台")
        print("=" * 60)
    
    def show_main_menu(self):
        """显示主菜单"""
        print("\n" + "=" * 60)
        print("🎮 主菜单")
        print("=" * 60)
        print("📊 状态监控:")
        print("  1. status     - 查看所有账户状态")
        print("  2. logs       - 查看账户日志")
        print("")
        print("🐳 容器管理:")
        print("  3. start      - 启动账户容器")
        print("  4. stop       - 停止账户容器")
        print("  5. restart    - 重启账户容器")
        print("  6. start-all  - 启动所有容器")
        print("  7. stop-all   - 停止所有容器")
        print("")
        print("⚙️ 策略管理:")
        print("  8. config     - 查看/修改策略配置")
        print("  9. enable     - 启用策略")
        print("  10. disable   - 禁用策略")
        print("  11. update    - 更新策略参数")
        print("")
        print("🎯 策略控制:")
        print("  12. add       - 添加策略到账户")
        print("  13. remove    - 从账户移除策略")
        print("  14. emergency - 紧急停止所有策略")
        print("")
        print("🔧 系统管理:")
        print("  15. reload    - 重新加载配置")
        print("  16. backup    - 备份配置和数据")
        print("  0. quit       - 退出程序")
        print("=" * 60)
    
    def run(self):
        """运行交互式控制台"""
        while True:
            try:
                self.show_main_menu()
                choice = input("\n请选择操作 (输入数字或命令): ").strip()
                
                if choice in ['0', 'quit', 'exit']:
                    print("👋 再见！")
                    break
                elif choice in ['1', 'status']:
                    self.cmd_show_status()
                elif choice in ['2', 'logs']:
                    self.cmd_show_logs()
                elif choice in ['3', 'start']:
                    self.cmd_start_container()
                elif choice in ['4', 'stop']:
                    self.cmd_stop_container()
                elif choice in ['5', 'restart']:
                    self.cmd_restart_container()
                elif choice in ['6', 'start-all']:
                    self.cmd_start_all()
                elif choice in ['7', 'stop-all']:
                    self.cmd_stop_all()
                elif choice in ['8', 'config']:
                    self.cmd_show_config()
                elif choice in ['9', 'enable']:
                    self.cmd_enable_strategy()
                elif choice in ['10', 'disable']:
                    self.cmd_disable_strategy()
                elif choice in ['11', 'update']:
                    self.cmd_update_strategy()
                elif choice in ['12', 'add']:
                    self.cmd_add_strategy()
                elif choice in ['13', 'remove']:
                    self.cmd_remove_strategy()
                elif choice in ['14', 'emergency']:
                    self.cmd_emergency_stop()
                elif choice in ['15', 'reload']:
                    self.cmd_reload_config()
                elif choice in ['16', 'backup']:
                    self.cmd_backup()
                else:
                    print("❌ 无效选择，请重新输入")
                    
            except KeyboardInterrupt:
                print("\n👋 再见！")
                break
            except Exception as e:
                print(f"❌ 操作异常: {e}")
    
    def cmd_show_status(self):
        """显示所有账户状态"""
        print("\n📊 账户状态总览")
        print("-" * 80)
        
        accounts = self.config_manager.get_all_accounts()
        
        for account_id in accounts:
            print(f"\n🔹 {account_id}")
            
            # 容器状态
            container_status = self.container_controller.get_container_status(account_id)
            status_icon = "🟢" if container_status['running'] else "🔴"
            print(f"  容器: {status_icon} {container_status['status']}")
            
            # 执行器状态
            if container_status['running']:
                executor_status = self.account_monitor.get_account_status(account_id)
                if 'error' not in executor_status:
                    uptime = executor_status.get('stats', {}).get('uptime_str', 'N/A')
                    orders = executor_status.get('stats', {}).get('total_orders', 0)
                    trades = executor_status.get('stats', {}).get('total_trades', 0)
                    strategies = executor_status.get('martin_strategies_count', 0)
                    
                    print(f"  执行器: 🟢 运行中 | 运行时间: {uptime}")
                    print(f"  策略: {strategies}个 | 订单: {orders} | 成交: {trades}")
                    
                    # 策略详情
                    martin_strategies = executor_status.get('martin_strategies', {})
                    for strategy_key, strategy_info in martin_strategies.items():
                        symbol = strategy_info['symbol']
                        mode = strategy_info['mode']
                        position = strategy_info['position_size']
                        avg_price = strategy_info['avg_price']
                        add_count = strategy_info['add_count']
                        
                        print(f"    📈 {symbol} ({mode}): 仓位={position:.6f} 成本={avg_price:.4f} 加仓={add_count}次")
                else:
                    print(f"  执行器: ❌ {executor_status['error']}")
            else:
                print(f"  执行器: ⭕ 容器未运行")
        
        print("-" * 80)
    
    def cmd_show_logs(self):
        """显示账户日志"""
        accounts = self.config_manager.get_all_accounts()
        
        print("\n可用账户:")
        for i, account_id in enumerate(accounts, 1):
            print(f"  {i}. {account_id}")
        
        try:
            choice = input("\n请选择账户 (输入数字或账户ID): ").strip()
            
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(accounts):
                    account_id = accounts[index]
                else:
                    print("❌ 无效选择")
                    return
            else:
                account_id = choice.upper()
                if account_id not in accounts:
                    print("❌ 账户不存在")
                    return
            
            lines = input("请输入日志行数 (默认50): ").strip()
            lines = int(lines) if lines.isdigit() else 50
            
            print(f"\n📋 {account_id} 最近 {lines} 行日志:")
            print("-" * 80)
            logs = self.container_controller.get_container_logs(account_id, lines)
            print(logs)
            print("-" * 80)
            
        except ValueError:
            print("❌ 输入无效")
    
    def cmd_start_container(self):
        """启动容器"""
        accounts = self.config_manager.get_all_accounts()
        
        print("\n可用账户:")
        for i, account_id in enumerate(accounts, 1):
            print(f"  {i}. {account_id}")
        
        choice = input("\n请选择要启动的账户: ").strip()
        account_id = self._parse_account_choice(choice, accounts)
        
        if account_id:
            self.container_controller.start_container(account_id)
    
    def cmd_stop_container(self):
        """停止容器"""
        accounts = self.config_manager.get_all_accounts()
        
        print("\n可用账户:")
        for i, account_id in enumerate(accounts, 1):
            print(f"  {i}. {account_id}")
        
        choice = input("\n请选择要停止的账户: ").strip()
        account_id = self._parse_account_choice(choice, accounts)
        
        if account_id:
            self.container_controller.stop_container(account_id)
    
    def cmd_restart_container(self):
        """重启容器"""
        accounts = self.config_manager.get_all_accounts()
        
        print("\n可用账户:")
        for i, account_id in enumerate(accounts, 1):
            print(f"  {i}. {account_id}")
        
        choice = input("\n请选择要重启的账户: ").strip()
        account_id = self._parse_account_choice(choice, accounts)
        
        if account_id:
            self.container_controller.restart_container(account_id)
    
    def cmd_start_all(self):
        """启动所有容器"""
        accounts = self.config_manager.get_all_accounts()
        
        confirm = input(f"确认启动所有 {len(accounts)} 个账户容器? (y/N): ").strip().lower()
        if confirm in ['y', 'yes']:
            for account_id in accounts:
                print(f"启动 {account_id}...")
                self.container_controller.start_container(account_id)
                time.sleep(2)  # 避免同时启动过多容器
    
    def cmd_stop_all(self):
        """停止所有容器"""
        accounts = self.config_manager.get_all_accounts()
        
        confirm = input(f"确认停止所有 {len(accounts)} 个账户容器? (y/N): ").strip().lower()
        if confirm in ['y', 'yes']:
            for account_id in accounts:
                print(f"停止 {account_id}...")
                self.container_controller.stop_container(account_id)
    
    def cmd_show_config(self):
        """显示策略配置"""
        print("\n⚙️ 当前策略配置")
        print("-" * 80)
        
        martin_defaults = self.config_manager.global_config.get('strategy_config', {}).get('martin_defaults', {})
        
        for symbol, modes in martin_defaults.items():
            print(f"\n📊 {symbol}:")
            
            for mode, config in modes.items():
                status = "✅ 启用" if config.get('enabled', False) else "❌ 禁用"
                print(f"  {mode}: {status}")
                print(f"    杠杆: {config.get('lever', 0)}x")
                print(f"    首次保证金: {config.get('first_margin', 0)} USDT")
                print(f"    最大加仓: {config.get('adding_number', 0)} 次")
                print(f"    止盈目标: {config.get('profit_target', 0)*100:.1f}%")
                print(f"    加仓触发: {config.get('opp_ratio', 0)*100:.1f}%")
        
        print("-" * 80)
    
    def cmd_enable_strategy(self):
        """启用策略"""
        symbol, mode = self._select_strategy()
        if symbol and mode:
            if self.config_manager.toggle_strategy(symbol, mode, True):
                print(f"✅ 已启用 {symbol} {mode} 策略")
                self._broadcast_config_update()
            else:
                print("❌ 启用失败")
    
    def cmd_disable_strategy(self):
        """禁用策略"""
        symbol, mode = self._select_strategy()
        if symbol and mode:
            if self.config_manager.toggle_strategy(symbol, mode, False):
                print(f"✅ 已禁用 {symbol} {mode} 策略")
                self._broadcast_config_update()
            else:
                print("❌ 禁用失败")
    
    def cmd_emergency_stop(self):
        """紧急停止所有策略"""
        accounts = self.config_manager.get_all_accounts()
        
        confirm = input("⚠️ 确认紧急停止所有账户的策略? (y/N): ").strip().lower()
        if confirm in ['y', 'yes']:
            for account_id in accounts:
                result = self.account_monitor.send_command(account_id, 'emergency_stop')
                if 'error' not in result:
                    print(f"✅ {account_id} 紧急停止成功")
                else:
                    print(f"❌ {account_id} 紧急停止失败: {result['error']}")
    
    def cmd_reload_config(self):
        """重新加载配置"""
        print("🔄 重新加载配置...")
        self.config_manager.global_config = self.config_manager.load_global_config()
        self.config_manager.accounts_config = self.config_manager.load_accounts_config()
        print("✅ 配置重新加载完成")
    
    def _select_strategy(self) -> tuple:
        """选择策略 (返回 symbol, mode)"""
        martin_defaults = self.config_manager.global_config.get('strategy_config', {}).get('martin_defaults', {})
        
        print("\n可用策略:")
        strategies = []
        index = 1
        
        for symbol, modes in martin_defaults.items():
            for mode in modes.keys():
                strategies.append((symbol, mode))
                status = "✅" if modes[mode].get('enabled', False) else "❌"
                print(f"  {index}. {symbol} {mode} {status}")
                index += 1
        
        try:
            choice = input("\n请选择策略 (输入数字): ").strip()
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(strategies):
                    return strategies[index]
            
            print("❌ 无效选择")
            return None, None
            
        except ValueError:
            print("❌ 输入无效")
            return None, None
    
    def _parse_account_choice(self, choice: str, accounts: List[str]) -> Optional[str]:
        """解析账户选择"""
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(accounts):
                return accounts[index]
        elif choice.upper() in accounts:
            return choice.upper()
        
        print("❌ 无效选择")
        return None
    
    def _broadcast_config_update(self):
        """广播配置更新到所有运行中的容器"""
        accounts = self.config_manager.get_all_accounts()
        
        for account_id in accounts:
            # 检查容器是否运行
            container_status = self.container_controller.get_container_status(account_id)
            if container_status['running']:
                # 发送配置更新命令
                result = self.account_monitor.send_command(account_id, 'reload_config')
                if 'error' not in result:
                    print(f"✅ {account_id} 配置已更新")
                else:
                    print(f"⚠️ {account_id} 配置更新失败: {result['error']}")


def main():
    """主函数"""
    try:
        manager = InteractiveManager()
        manager.run()
    except KeyboardInterrupt:
        print("\n👋 程序被用户中断")
    except Exception as e:
        print(f"❌ 程序异常: {e}")


if __name__ == "__main__":
    main()