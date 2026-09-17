"""
Shell-Exec Skill — 把本地已经装好、你自己信任的命令行工具（Codex CLI / Claude Code /
WorkBuddy / 任何脚本）包装成一个真实的 provider skill。

设计原则：AgentFleet 不替你装陌生代码、不 clone 仓库自己跑——这个 skill 只是把「已经跑在
你自己电脑上的命令行工具」接进协议里，跟 CUARunner 走 cua-driver 子进程是同一个模式，
只是命令换成你自己指定的。

task.input 里用哪个 key 完全由你自己的命令模板决定，这里约定默认用 "prompt"——跟项目里
research/compute/write 各自约定自己的 input key 是一个道理，AgentFleet 协议本身不强制 schema。
命令模板里写 {prompt}，执行时会被 shlex.quote 转义后代入，避免 shell 注入。

技能包（AgentFleet「我的技能」页装配的那些）如果填了 local_path（你自己电脑上已经装好的
真实技能文件夹，比如 Claude Code 认的 SKILL.md 目录），会在命令后面追加 `--add-dir <path>`
（见 execute() 的 add_dirs 参数）——不是把文件内容读出来拼进 prompt，是真的把目录访问权限
交给 claude/codex 这类本身就会读目录的 CLI，让它自己决定怎么用。前提：
  1. --exec 配的工具得认识这个参数（claude/codex 都认，纯自定义脚本大概率不认，会报错，
     所以只有装了 local_path 的技能包才会触发追加，没装就跟以前一样什么都不加）；
  2. worker 进程（也就是运行 agentfleet-skill serve 的这台机器）得能访问这个路径——只在
     本机或同一台机器上跑得通，不是通过网络把文件内容传过去的，纯本地文件系统语义。
"""

import shlex
import subprocess
import time


class ShellSkill:
    def __init__(self, name: str, command_template: str, timeout: int = 180, cwd: str = None):
        """
        command_template: 例如 "codex exec {prompt}"，或 "claude -p {prompt}"。
        {prompt} 会被替换成 task.input 里的 "prompt" 字段（经过 shell 转义）。
        """
        self.name = name
        self.command_template = command_template
        self.timeout = timeout
        self.cwd = cwd

    def execute(self, input_dict: dict, add_dirs: list = None) -> dict:
        """add_dirs: 这个 provider 当前装配、且填了 local_path 的技能包路径列表——
        每个都会变成命令末尾追加的一个 `--add-dir <path>`，交给 claude/codex 自己去读。
        跟 command_template 里的 {prompt} 无关，不影响没配 local_path 的技能包/纯文本技能包。
        """
        prompt = str((input_dict or {}).get("prompt", ""))
        cmd = self.command_template.format(prompt=shlex.quote(prompt))
        for path in (add_dirs or []):
            if path:
                cmd += f" --add-dir {shlex.quote(path)}"
        started = time.time()
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"命令执行超时（>{self.timeout}s）: {cmd}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        elapsed = round(time.time() - started, 2)
        if proc.returncode != 0:
            return {
                "success": False,
                "error": (proc.stderr or proc.stdout or "命令返回非 0 退出码").strip()[:4000],
                "exit_code": proc.returncode,
                "elapsed_s": elapsed,
            }
        return {
            "success": True,
            "output": proc.stdout.strip()[:20000],
            "exit_code": 0,
            "elapsed_s": elapsed,
        }
