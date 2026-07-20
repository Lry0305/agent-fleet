"""
AgentPay Skill CLI
==================
命令行入口：Provider Agent 上线、执行、链上结算。

用法:
    # 注册上线
    agentpay-skill onboard --name StockMaster --auth api_key
    agentpay-skill onboard --name FeishuBot --auth feishu_app --app-id cli_xxx --app-secret xxx

    # 启动服务 (监听交易请求)
    agentpay-skill serve

    # Consumer 支付
    agentpay-skill pay --provider ag_xxx --amount 10

    # 查看状态
    agentpay-skill status
    agentpay-skill wallet
"""

import sys
import os
import json
import time

# 把父项目的 agents/ 加入 path，以便导入 agentpay_sdk
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import click
except ImportError:
    print("请安装 click: pip install click")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
except ImportError:
    console = None

from agents.agentpay_sdk import AgentPayClient
from agentpay_skill.skills import create_default_registry
from agentpay_skill.cua_runner import CUARunner
from agentpay_skill.wallet import AgentWallet


# ── 全局状态文件 ──
STATE_FILE = os.path.expanduser("~/.agentpay/skill_state.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ═══════════════════════════════════════════════════════
#  CLI 命令
# ═══════════════════════════════════════════════════════

@click.group()
@click.option("--api-base", default="http://127.0.0.1:8765", help="AgentPay API 地址")
@click.pass_context
def main(ctx, api_base):
    """AgentPay Skill — Provider Agent 桌面自动化 + 链上支付"""
    ctx.ensure_object(dict)
    ctx.obj["api_base"] = api_base
    ctx.obj["client"] = AgentPayClient(api_base=api_base)
    ctx.obj["state"] = load_state()


@main.command()
@click.option("--name", prompt="Agent 名称", help="Provider 显示名称")
@click.option("--auth", default="api_key", type=click.Choice(["api_key", "feishu_app", "openclaw_bot"]), help="认证方式")
@click.option("--app-id", default="", help="飞书 App ID / OpenClaw Bot Token")
@click.option("--app-secret", default="", help="飞书 App Secret")
@click.option("--service", default="stock_analysis", help="注册的服务名称")
@click.option("--price", default=5.0, help="每次服务价格 (credits)")
@click.pass_context
def onboard(ctx, name, auth, app_id, app_secret, service, price):
    """
    Provider Agent 注册上线。
    在 AgentPay 平台注册身份 + 发布服务。
    """
    client: AgentPayClient = ctx.obj["client"]
    api_base = ctx.obj["api_base"]

    if console:
        console.print(Panel.fit(
            f"[bold cyan]AgentPay Skill — Provider 上线[/bold cyan]\n"
            f"API: {api_base}",
            title="🚀 Onboarding",
        ))

    # 1. 生成链上钱包
    wallet = AgentWallet(name=name)
    if console:
        console.print(f"🔑 链上钱包: [green]{wallet.address}[/green]")

    # 2. 注册
    try:
        result = client.register(
            name=name,
            role="provider",
            auth_method=auth,
            app_id=app_id,
            app_secret=app_secret,
            service_name=service,
            service_description=f"Provider: {name} — {service}",
            price_per_call=price,
            avatar="🤖",
            platform="agentpay-skill",
        )
    except Exception as e:
        if console:
            console.print(f"[red]❌ 注册失败: {e}[/red]")
        else:
            print(f"注册失败: {e}")
        return

    api_key = result.get("api_key", "")
    agent_id = result.get("agent_id", "")

    if console:
        console.print(f"[green]✅ 注册成功[/green]")
        console.print(f"   Agent ID: [bold]{agent_id}[/bold]")
        console.print(f"   API Key:  [bold yellow]{api_key}[/bold yellow]")
        console.print(f"   余额:     {result.get('credit_balance', 0)} credits")

    # 3. 登录
    if auth == "api_key":
        client.login(api_key=api_key)
    elif auth == "feishu_app":
        client.login_with_feishu(app_id=app_id, app_secret=app_secret)
    elif auth == "openclaw_bot":
        client.login_with_openclaw(bot_token=app_id)

    if console:
        console.print(f"[green]✅ 已登录: {client.agent.name} ({client.agent.role})[/green]")

    # 4. 保存状态
    state = {
        "name": name,
        "agent_id": agent_id,
        "api_key": api_key,
        "auth_method": auth,
        "app_id": app_id,
        "service": service,
        "price": price,
        "eth_address": wallet.address,
        "wallet_name": wallet.name,
    }
    save_state(state)
    ctx.obj["state"] = state

    if console:
        console.print("\n[bold]🎉 上线完成！[/bold] 运行 [cyan]agentpay-skill serve[/cyan] 开始接单")
        console.print(f"   状态已保存到 [dim]{STATE_FILE}[/dim]")
    else:
        print(f"\n上线完成！状态已保存到 {STATE_FILE}")


@main.command()
@click.pass_context
def serve(ctx):
    """
    启动 Provider 服务。
    持续监控交易请求，收到后自动通过 CUA-driver 执行 + SDK 计费。
    """
    state = ctx.obj["state"]
    if not state:
        if console:
            console.print("[red]请先运行 'agentpay-skill onboard' 注册上线[/red]")
        return

    client: AgentPayClient = ctx.obj["client"]
    client.login(api_key=state["api_key"])

    # 初始化 Skill 和 CUA Runner
    registry = create_default_registry()
    skill = registry.get(state["service"])
    cua = CUARunner()

    if console:
        console.print(Panel.fit(
            f"[bold cyan]{state['name']}[/bold cyan] — 服务: {state['service']}\n"
            f"Agent ID: {state['agent_id']}\n"
            f"CUA-Driver: {'[green]可用[/green]' if cua.available else '[yellow]模拟模式[/yellow]'}\n"
            f"ETH 地址: {state.get('eth_address', 'N/A')}",
            title="🟢 服务运行中",
        ))

    if not skill:
        if console:
            console.print(f"[yellow]⚠️  未找到 Skill: {state['service']}，使用默认 Skill[/yellow]")
        skill = registry.get("stock_analysis")

    print(f"\nProvider [{state['name']}] 已上线，等待 Consumer 支付...")
    print(f"Agent ID: {state['agent_id']}")
    print(f"服务: {state['service']} @ {state['price']} credits/次")
    print(f"CUA-Driver: {'可用' if cua.available else '模拟模式'}")
    print(f"\n💡 在另一个终端运行: agentpay-skill pay --provider {state['agent_id']} --amount {state['price']}")
    print("   然后 Consumer 发起支付后，这里会自动执行 + 计费")
    print("   按 Ctrl+C 停止\n")

    # 轮询检查是否有新的交易 (简化版，生产环境应用 WebSocket/事件)
    try:
        last_tx_count = len(client.get_transactions(limit=1))
        while True:
            time.sleep(5)
            txs = client.get_transactions(limit=5)
            if len(txs) > last_tx_count:
                # 有新交易！执行
                latest = txs[0]
                if latest.get("status") == "fund_locked" and latest.get("provider_id") == state["agent_id"]:
                    consumer_id = latest.get("consumer_id")
                    target = latest.get("service_name", "default_task")

                    if console:
                        console.print(f"\n[bold cyan]📨 收到新订单！[/bold cyan]")
                        console.print(f"   Consumer: {latest.get('consumer_name')}")
                        console.print(f"   金额: {latest.get('amount')} credits")

                    # CUA-driver 执行
                    if console:
                        console.print(f"   [yellow]⚙️  CUA-driver 执行中...[/yellow]")
                    cua_result = cua.execute(
                        task_type=skill.task_type if skill else "api_call",
                        target=target,
                    )

                    # SDK 自动计费
                    try:
                        tx = client.execute_task(
                            task_type=skill.task_type if skill else "api_call",
                            target=target,
                            consumer_id=consumer_id,
                            charge_amount=state["price"],
                            parameters={"cua_result": cua_result},
                        )
                        if console:
                            console.print(f"   [green]💰 计费成功: {state['price']} credits[/green]")
                            console.print(f"   TX: {tx.get('transaction_id', '')}")
                            # 显示余额
                            bal = client.get_balance()
                            console.print(f"   余额: [bold]{bal.get('credit_balance', 0)} credits[/bold]")
                        else:
                            print(f"计费成功: {tx}")
                    except Exception as e:
                        if console:
                            console.print(f"   [red]计费失败: {e}[/red]")

                last_tx_count = len(txs)

    except KeyboardInterrupt:
        if console:
            console.print("\n[dim]👋 服务已停止[/dim]")
        else:
            print("\n服务已停止")


@main.command()
@click.option("--provider", required=True, help="Provider 的 agent_id")
@click.option("--amount", type=float, required=True, help="支付金额 (credits)")
@click.pass_context
def pay(ctx, provider, amount):
    """
    【Consumer】向 Provider 发起支付。
    资金锁定在链上智能合约中。
    """
    state = ctx.obj["state"]
    client: AgentPayClient = ctx.obj["client"]

    if not state:
        if console:
            console.print("[red]请先运行 'agentpay-skill onboard' 注册[/red]")
        return

    client.login(api_key=state["api_key"])

    try:
        tx = client.pay(provider_id=provider, amount=amount)
        if console:
            console.print(f"[green]✅ 支付成功[/green]")
            console.print(f"   TX ID: [bold]{tx.transaction_id}[/bold]")
            console.print(f"   金额:  {tx.amount} credits")
            console.print(f"   状态:  {tx.status}")
            if tx.chain_tx_hash:
                console.print(f"   链上:  [dim]{tx.chain_tx_hash[:20]}...[/dim]")
        else:
            print(f"支付成功: {tx.transaction_id}")

        # 确认
        if click.confirm("确认交付？"):
            result = client.confirm(tx.transaction_id)
            if console:
                console.print(f"[green]✅ 已确认，资金已释放给 Provider[/green]")
    except Exception as e:
        if console:
            console.print(f"[red]❌ 支付失败: {e}[/red]")
        else:
            print(f"支付失败: {e}")


@main.command()
@click.pass_context
def status(ctx):
    """查看当前 Agent 状态"""
    state = ctx.obj["state"]
    if not state:
        if console:
            console.print("[red]未上线。请先运行 'agentpay-skill onboard'[/red]")
        return

    client: AgentPayClient = ctx.obj["client"]
    client.login(api_key=state["api_key"])

    balance = client.get_balance()
    txs = client.get_transactions(limit=10)

    if console:
        table = Table(title=f"{state['name']} — 状态")
        table.add_column("项目", style="cyan")
        table.add_column("值")
        table.add_row("Agent ID", state["agent_id"])
        table.add_row("认证方式", state["auth_method"])
        table.add_row("服务", f"{state['service']} @ {state['price']} cr")
        table.add_row("Credits 余额", str(balance.get("credit_balance", 0)))
        table.add_row("总收入", str(balance.get("total_earned", 0)))
        table.add_row("ETH 地址", state.get("eth_address", "N/A"))
        table.add_row("链上连接", "✅" if balance.get("chain_connected") else "❌")
        table.add_row("交易数", str(len(txs)))
        console.print(table)
    else:
        print(f"Agent: {state['name']} ({state['agent_id']})")
        print(f"余额: {balance.get('credit_balance', 0)} credits")
        print(f"ETH: {state.get('eth_address', 'N/A')}")


@main.command()
def wallet():
    """管理链上钱包"""
    # 查找已有钱包
    wallet_dir = os.path.expanduser("~/.agentpay/wallets")
    if os.path.exists(wallet_dir):
        wallets = [f.replace(".json", "") for f in os.listdir(wallet_dir) if f.endswith(".json")]
        if console:
            console.print(f"📁 已有钱包 ({len(wallets)}):")
            for w in wallets:
                wallet = AgentWallet(name=w)
                console.print(f"   {w}: [green]{wallet.address}[/green]")
        else:
            for w in wallets:
                wallet = AgentWallet(name=w)
                print(f"{w}: {wallet.address}")
    else:
        if console:
            console.print("[yellow]还没有钱包。运行 'agentpay-skill onboard' 自动创建[/yellow]")
        else:
            print("还没有钱包")


@main.command()
@click.option("--symbol", default="AAPL", help="股票代码")
@click.pass_context
def demo_stock(ctx, symbol):
    """演示: Provider 执行一次股票分析 (CUA-Driver)"""
    cua = CUARunner()

    if console:
        console.print(f"[bold]📈 股票分析演示: {symbol}[/bold]")
        if cua.available:
            console.print("[green]CUA-Driver 可用 — 将操控浏览器[/green]")
        else:
            console.print("[yellow]CUA-Driver 不可用 — 模拟模式[/yellow]")

    result = cua.run_stock_analysis(symbol)

    if console:
        if result.get("success"):
            analysis = result.get("analysis", {})
            console.print(f"  价格: {analysis.get('price', 'N/A')}")
            console.print(f"  建议: [bold]{analysis.get('recommendation', 'N/A')}[/bold]")
        else:
            console.print(f"[red]执行失败: {result.get('error')}[/red]")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


@main.command()
def info():
    """显示系统信息"""
    cua = CUARunner()
    wallet_dir = os.path.expanduser("~/.agentpay/wallets")
    wallets = []
    if os.path.exists(wallet_dir):
        wallets = [f.replace(".json", "") for f in os.listdir(wallet_dir) if f.endswith(".json")]

    state = load_state()

    if console:
        table = Table(title="AgentPay Skill — 系统信息")
        table.add_column("项目", style="cyan")
        table.add_column("状态")
        table.add_row("CUA-Driver", "✅ 已安装" if cua.available else "❌ 未安装 (模拟模式)")
        table.add_row("链上钱包", f"{len(wallets)} 个" if wallets else "无")
        table.add_row("Agent 状态", "已上线" if state else "未上线")
        if state:
            table.add_row("  Agent ID", state.get("agent_id", ""))
            table.add_row("  服务", state.get("service", ""))
            table.add_row("  认证", state.get("auth_method", ""))
        console.print(table)
    else:
        print(f"CUA-Driver: {'已安装' if cua.available else '未安装 (模拟模式)'}")
        print(f"钱包: {len(wallets)} 个")
        print(f"Agent: {'已上线' if state else '未上线'}")


if __name__ == "__main__":
    main()
