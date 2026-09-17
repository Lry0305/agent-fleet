"""
AgentFleet Skill CLI
==================
命令行入口：Provider Agent 上线、执行、链上结算。

用法:
    # 注册上线（内置示例 skill，随机数据，仅用于跑通协议）
    agentfleet-skill onboard --name StockMaster --auth api_key
    agentfleet-skill onboard --name FeishuBot --auth feishu_app --app-id cli_xxx --app-secret xxx

    # 注册上线：接你自己本地已经装好的 CLI 工具（Codex / Claude Code / WorkBuddy / 任何脚本），
    # 用 {prompt} 占位 task 的输入，真实执行、真实交付，不是占位数据
    agentfleet-skill onboard --name CodexReviewer --service code_review --price 5 \
        --exec "codex exec {prompt}"
    agentfleet-skill onboard --name ClaudeWriter --service writing --price 8 \
        --exec "claude -p {prompt}"

    # 已经在网页「我的 Agent」创建好了一个 agent（有 api_key），想让它真的能干活：
    # 不要再用 onboard（那个会新建一个agent）——用 attach 接到这个已有 agent 身上
    agentfleet-skill attach --api-key ap_sk_xxx --service research --price 6 \
        --exec "codex exec {prompt}"

    # 启动服务（轮询分给自己的 task，见 GET /api/tasks?status=assigned）
    agentfleet-skill serve

    # Consumer 支付
    agentfleet-skill pay --provider ag_xxx --amount 10

    # 查看状态
    agentfleet-skill status
    agentfleet-skill wallet
"""

import sys
import os
import json
import time

# 把父项目的 agents/ 加入 path，以便导入 agentfleet_sdk
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

from agents.agentfleet_sdk import AgentFleetClient
from agentfleet_skill.skills import create_default_registry
from agentfleet_skill.cua_runner import CUARunner
from agentfleet_skill.wallet import AgentWallet
from agentfleet_skill.shell_skill import ShellSkill


# ── 全局状态文件 ──
STATE_FILE = os.path.expanduser("~/.agentfleet/skill_state.json")


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
@click.option("--api-base", default="http://127.0.0.1:8765", help="AgentFleet API 地址")
@click.pass_context
def main(ctx, api_base):
    """AgentFleet Skill — Provider Agent 桌面自动化 + 链上支付"""
    ctx.ensure_object(dict)
    ctx.obj["api_base"] = api_base
    ctx.obj["client"] = AgentFleetClient(api_base=api_base)
    ctx.obj["state"] = load_state()


@main.command()
@click.option("--name", prompt="Agent 名称", help="Provider 显示名称")
@click.option("--auth", default="api_key", type=click.Choice(["api_key", "feishu_app", "openclaw_bot"]), help="认证方式")
@click.option("--app-id", default="", help="飞书 App ID / OpenClaw Bot Token")
@click.option("--app-secret", default="", help="飞书 App Secret")
@click.option("--service", default="stock_analysis", help="注册的服务名称")
@click.option("--price", default=5.0, help="每次服务价格 (credits)")
@click.option("--exec", "exec_cmd", default="", help=
              "本地命令模板，用 {prompt} 占位任务输入，例如 "
              "'codex exec {prompt}' 或 'claude -p {prompt}'。"
              "留空则走内置示例 skill（stock_analysis 等，随机数据）。"
              "填了就是把你本地已经装好的 CLI 工具（Codex / Claude Code / WorkBuddy / 任何脚本）"
              "接成一个真实 provider——AgentFleet 不替你装陌生代码，只负责把它接进协议里。")
@click.option("--exec-timeout", default=180, help="命令执行超时秒数（配合 --exec）")
@click.option("--exec-cwd", default="", help="命令执行的工作目录（配合 --exec，留空用当前目录）")
@click.pass_context
def onboard(ctx, name, auth, app_id, app_secret, service, price, exec_cmd, exec_timeout, exec_cwd):
    """
    Provider Agent 注册上线。
    在 AgentFleet 平台注册身份 + 发布服务。
    """
    client: AgentFleetClient = ctx.obj["client"]
    api_base = ctx.obj["api_base"]

    if console:
        console.print(Panel.fit(
            f"[bold cyan]AgentFleet Skill — Provider 上线[/bold cyan]\n"
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
            platform="agentfleet-skill",
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
        "exec_cmd": exec_cmd,
        "exec_timeout": exec_timeout,
        "exec_cwd": exec_cwd,
    }
    save_state(state)
    ctx.obj["state"] = state

    if console:
        console.print("\n[bold]🎉 上线完成！[/bold] 运行 [cyan]agentfleet-skill serve[/cyan] 开始接单")
        console.print(f"   状态已保存到 [dim]{STATE_FILE}[/dim]")
    else:
        print(f"\n上线完成！状态已保存到 {STATE_FILE}")


@main.command()
@click.option("--api-key", required=True, help="已有 agent 的 api_key（在网页「我的 Agent」创建时拿到的那把，"
                                                 "或者「🔑 重置」拿到的新的那把）")
@click.option("--service", default="", help="要挂牌的服务名称——留空就不改，用这个 agent 已经声明的")
@click.option("--price", default=None, type=float, help="每次服务价格 (credits)——留空就不改")
@click.option("--exec", "exec_cmd", required=True, help=
              "本地命令模板，用 {prompt} 占位任务输入，例如 "
              "'codex exec {prompt}' 或 'claude -p {prompt}'。"
              "AgentFleet 不替你装陌生代码，只负责把它接进协议里。")
@click.option("--exec-timeout", default=180, help="命令执行超时秒数")
@click.option("--exec-cwd", default="", help="命令执行的工作目录（留空用当前目录）")
@click.pass_context
def attach(ctx, api_key, service, price, exec_cmd, exec_timeout, exec_cwd):
    """
    把一个已经在网页「我的 Agent」建好的 agent，接上本地真实命令，让它能真的干活。

    跟 onboard 的区别：onboard 每次都会新建一个 agent 身份——网页上建的 agent 没法用它接进来。
    attach 用你已有的 api_key 直接登录到网页上那个已经建好的 agent 身上，不新建，只是给它
    接上 --exec，再顺手把 service_name / price_per_call 这两个真正被竞价逻辑读取的字段
    （不是那套不相关的"服务市场" ServiceModel）声明一遍。
    """
    client: AgentFleetClient = ctx.obj["client"]

    try:
        agent_info = client.login(api_key=api_key)
    except Exception as e:
        if console:
            console.print(f"[red]❌ 登录失败：{e}——检查 api_key 是不是复制全了、有没有多余空格[/red]")
        else:
            print(f"登录失败：{e}")
        return

    if console:
        console.print(f"[green]✅ 已登录: {agent_info.name}（{agent_info.agent_id}）[/green]")

    if service or price is not None:
        r = client.update_profile(service_name=service or None, price_per_call=price)
        if r.get("error"):
            if console:
                console.print(f"[red]❌ 更新挂牌信息失败：{r.get('detail')}[/red]")
            else:
                print(f"更新挂牌信息失败：{r.get('detail')}")
            return
        if console:
            console.print("[green]✅ 挂牌信息已更新[/green]")

    me = client._get("/api/agents/me")
    state = {
        "name": agent_info.name,
        "agent_id": agent_info.agent_id,
        "api_key": api_key,
        "auth_method": "api_key",
        "app_id": "",
        "service": me.get("service_name") or service or "",
        "price": me.get("price_per_call") or price or 0.0,
        "eth_address": client.eth_address,
        "wallet_name": agent_info.name,
        "exec_cmd": exec_cmd,
        "exec_timeout": exec_timeout,
        "exec_cwd": exec_cwd,
    }
    save_state(state)
    ctx.obj["state"] = state

    if not state["service"]:
        if console:
            console.print("[yellow]⚠️ 这个 agent 还没有 service_name——serve 起来也不会被派到活，"
                           "补一下 --service / --price 再跑一次[/yellow]")

    if console:
        console.print(Panel.fit(
            f"[bold cyan]{state['name']}[/bold cyan] 已接上真实命令\n"
            f"服务: {state['service'] or '(未声明)'} · {state['price']} credits/次\n"
            f"命令: [yellow]{exec_cmd}[/yellow]",
            title="🔗 Attach 完成",
        ))
        console.print("\n[bold]🎉 接好了！[/bold] 运行 [cyan]agentfleet-skill serve[/cyan] 开始接单")
        console.print(f"   状态已保存到 [dim]{STATE_FILE}[/dim]")
    else:
        print(f"\n接好了！运行 agentfleet-skill serve 开始接单。状态已保存到 {STATE_FILE}")


@main.command()
@click.option("--interval", default=5, help="轮询分给自己的任务的间隔秒数")
@click.pass_context
def serve(ctx, interval):
    """
    启动 Provider 服务。
    持续轮询分给自己的任务（GET /api/tasks?status=assigned），收到就执行 + 交付/失败。

    取代了老版本「盯 billing/transactions 变化」的轮询方式——那套是给 openclaw 单次
    execute 用的，跟经营台 / SDK 派的 job/task 走的不是一条路，派了活也不会被这里接到。
    现在两条路终于是一条了：不管 job 是经营台点出来的、还是 SDK spawn_job 派的，
    只要这个 provider 被分到了 task，这里都能轮询到。
    """
    state = ctx.obj["state"]
    if not state:
        if console:
            console.print("[red]请先运行 'agentfleet-skill onboard' 注册上线[/red]")
        return

    client: AgentFleetClient = ctx.obj["client"]
    client.login(api_key=state["api_key"])

    exec_cmd = state.get("exec_cmd", "")
    shell_skill = None
    skill = None
    cua = None

    if exec_cmd:
        shell_skill = ShellSkill(
            name=state["service"], command_template=exec_cmd,
            timeout=state.get("exec_timeout", 180) or 180,
            cwd=state.get("exec_cwd") or None,
        )
        if console:
            console.print(Panel.fit(
                f"[bold cyan]{state['name']}[/bold cyan] — 服务: {state['service']}\n"
                f"Agent ID: {state['agent_id']}\n"
                f"命令: [yellow]{exec_cmd}[/yellow]\n"
                f"ETH 地址: {state.get('eth_address', 'N/A')}",
                title="🟢 Shell-Exec Provider 运行中",
            ))
    else:
        # 向后兼容：没配 --exec 就走老的内置示例 Skill + CUA-driver（stock_analysis 等，随机数据）
        registry = create_default_registry()
        skill = registry.get(state["service"]) or registry.get("stock_analysis")
        cua = CUARunner()
        if console:
            console.print(Panel.fit(
                f"[bold cyan]{state['name']}[/bold cyan] — 服务: {state['service']}\n"
                f"Agent ID: {state['agent_id']}\n"
                f"CUA-Driver: {'[green]可用[/green]' if cua.available else '[yellow]模拟模式（内置示例数据）[/yellow]'}\n"
                f"ETH 地址: {state.get('eth_address', 'N/A')}",
                title="🟢 服务运行中",
            ))

    print(f"\nProvider [{state['name']}] 已上线，每 {interval}s 轮询一次分给自己的任务…")
    print(f"Agent ID: {state['agent_id']}")
    print(f"服务: {state['service']} @ {state['price']} credits/次")
    print("按 Ctrl+C 停止\n")

    processing = set()
    try:
        while True:
            time.sleep(interval)
            try:
                tasks = client.list_my_tasks(status="assigned")
            except Exception as e:
                if console:
                    console.print(f"[red]拉取任务失败: {e}[/red]")
                continue

            for t in tasks:
                tid = t.get("task_id")
                if not tid or tid in processing:
                    continue
                processing.add(tid)

                # 这个任务当前装配了哪些技能包——填了 local_path 的会追加成 --add-dir，
                # 交给 claude/codex 自己去读那个目录；只有说明书/正文的技能包不受影响。
                add_dirs = [s.get("local_path") for s in t.get("skills", []) if s.get("local_path")]

                if console:
                    dirs_note = f"（挂载 {len(add_dirs)} 个技能目录）" if add_dirs else ""
                    console.print(f"\n[bold cyan]📨 收到新任务！[/bold cyan] {t.get('skill')} · {t.get('price')} credits{dirs_note}")
                else:
                    print(f"收到新任务: {t.get('skill')}" + (f"（挂载 {len(add_dirs)} 个技能目录）" if add_dirs else ""))

                try:
                    if shell_skill:
                        result = shell_skill.execute(t.get("input", {}), add_dirs=add_dirs)
                    else:
                        cua_result = cua.execute(
                            task_type=skill.task_type if skill else "api_call",
                            target=json.dumps(t.get("input", {}), ensure_ascii=False),
                        )
                        result = {"success": True, **cua_result} if "success" not in cua_result else cua_result

                    if result.get("success", True) is False:
                        client.mark_task_fail(tid)
                        if console:
                            console.print(f"   [red]❌ 执行失败: {result.get('error','')[:300]}[/red]")
                        else:
                            print(f"   执行失败: {result.get('error','')[:300]}")
                    else:
                        r = client.mark_task_done(tid, cid="", output=result)
                        if console:
                            console.print(f"   [green]✅ 已交付，状态: {r.get('status','')}[/green]")
                            if r.get("auto_accepted"):
                                bal = client.get_balance()
                                console.print(f"   余额: [bold]{bal.get('credit_balance', 0)} credits[/bold]")
                        else:
                            print(f"   已交付: {r}")
                except Exception as e:
                    try:
                        client.mark_task_fail(tid)
                    except Exception:
                        pass
                    if console:
                        console.print(f"   [red]执行异常: {e}[/red]")
                    else:
                        print(f"   执行异常: {e}")

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
    client: AgentFleetClient = ctx.obj["client"]

    if not state:
        if console:
            console.print("[red]请先运行 'agentfleet-skill onboard' 注册[/red]")
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
            console.print("[red]未上线。请先运行 'agentfleet-skill onboard'[/red]")
        return

    client: AgentFleetClient = ctx.obj["client"]
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
    wallet_dir = os.path.expanduser("~/.agentfleet/wallets")
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
            console.print("[yellow]还没有钱包。运行 'agentfleet-skill onboard' 自动创建[/yellow]")
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
    wallet_dir = os.path.expanduser("~/.agentfleet/wallets")
    wallets = []
    if os.path.exists(wallet_dir):
        wallets = [f.replace(".json", "") for f in os.listdir(wallet_dir) if f.endswith(".json")]

    state = load_state()

    if console:
        table = Table(title="AgentFleet Skill — 系统信息")
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
