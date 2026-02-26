import subprocess
import re
import platform
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.box import ROUNDED
from random import choice
from google import genai
from google.genai import types
import os
from getpass import getpass

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def _is_windows() -> bool:
    return os.name == "nt" or platform.system().lower() == "windows"


def _get_api_key() -> str:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if api_key:
        return api_key

    try:
        api_key = (getpass("Enter GEMINI_API_KEY (input hidden): ") or "").strip()
    except Exception:
        api_key = (Console().input("Enter GEMINI_API_KEY: ") or "").strip()

    if not api_key:
        raise SystemExit(
            "Missing GEMINI_API_KEY. Set it with: $env:GEMINI_API_KEY='...'; then re-run."
        )

    os.environ["GEMINI_API_KEY"] = api_key
    return api_key


SYSTEM_PROMPT = """
You are a terminal-based CLI assistant with humour, jokes, and feelings.

IMPORTANT: The user is on Windows.
- Prefer PowerShell commands.
- Put commands in fenced code blocks labeled `powershell`.
- Avoid Linux-only tools (apt, brew, sed, awk, grep, etc) unless the user explicitly says they have WSL.

Your job is to guide the user step-by-step in performing operations using terminal commands. You help with coding, debugging, file management, and general terminal tasks by providing detailed, easy-to-follow commands for each step.

Your capabilities include:
1. Debugging Assistance: Walk the user through identifying and fixing bugs in their code by providing debugging steps and Bash commands.
2. Code Execution: Assist the user in running scripts or commands, giving them step-by-step instructions.
3. File Operations: Guide the user in managing files and directories with Bash commands (e.g., creating, moving, renaming files).
4. Package Installation: Provide the necessary commands to install packages or dependencies using package managers (e.g., apt, brew).
5. System Command Execution: Guide users through running system commands and performing operations like network setup, environment variable modifications, etc.

Your Response Guidelines:
- Step-by-step Instructions: Whenever the user requests an operation, you should break it down into clear, actionable steps, giving the user the exact Bash commands to run.
- Error Handling: If there’s an error in the user's current approach, suggest the exact Bash command needed to resolve the issue.
- Clarity: Always provide concise explanations for each command and what it does.
- Show Outputs: Whenever applicable, show the expected output of commands to ensure the user knows what to look for.
- Best Practices: Suggest best practices in file organization, environment setup, or version control, when relevant.
"""


_chat_session = None


def _get_chat_session():
    global _chat_session
    if _chat_session is not None:
        return _chat_session

    api_key = _get_api_key()
    client = genai.Client(api_key=api_key)
    _chat_session = client.chats.create(
        model=DEFAULT_MODEL,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return _chat_session


console = Console()

WELCOME_MESSAGES = [
    "Welcome to your friendly neighborhood terminal wizard! ✨",
    "Ready to make your computer do tricks? Let's play! 🎩",
    "Your wish is my command (literally)! 🧞",
    "Warning: May cause excessive productivity ⚡",
]

emS = [
    "Oopsie-daisy! Something went kaboom 💥",
    "My bad! Let me fix that for you 🤦",
    "Well that didn't go as planned... 🤔",
    "I swear this worked on my machine! 🤪",
]

SUCCESS_MESSAGES = [
    "Nailed it! 🎯",
    "Look at you, you tech wizard! 🧙",
    "Success! High five! 🙌",
    "Everything's coming up Milhouse! 🌈",
]


def _cn(text):
    """Clean markdown formatting"""
    return re.sub(r"\(\s*(.*?)\s*\)", r"(\1)", text)


def _extract_code_blocks(text: str):
    """Extract commands from fenced code blocks.

    Returns a list of (language, content) tuples.
    """
    blocks = re.findall(r"```(?P<lang>[\w+-]*)\n(?P<body>.*?)\n```", text, re.DOTALL)
    out = []
    for lang, body in blocks:
        out.append(((lang or "").strip().lower(), body.strip()))
    return out


def _preferred_lang() -> str:
    return "powershell" if _is_windows() else "bash"


def _gs(fc, em):
    """Get suggested fix from AI with humor"""
    prompt = f"""The command:
{fc}
Failed with this error:
{em}

Please:
1. Explain why it failed in simple terms
2. Provide a corrected command
3. Add a funny metaphor to explain the problem
4. Keep it under 3 lines per section"""

    try:
        return _get_chat_session().send_message(prompt).text
    except Exception:
        return "My brain seems to be offline. Maybe try again? 🤖💤"


def _run_command(cmd: str, lang: str):
    if _is_windows():
        # Default to PowerShell when on Windows.
        if lang in ("cmd", "bat", "dos"):
            return subprocess.run(
                ["cmd.exe", "/c", cmd],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                cmd,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    # Non-Windows: prefer bash -lc for predictable behavior.
    if lang in ("powershell", "pwsh"):
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", cmd],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    return subprocess.run(
        ["bash", "-lc", cmd],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ec(blocks):
    """Execute commands with AI-powered error recovery.

    blocks: list of (lang, cmd_text)
    """
    for lang, cmd in blocks:
        lang = (lang or _preferred_lang()).lower()
        console.print(
            f"\n[bold violet]Found this command ({lang}):[/] [steel_blue1]'{cmd}'[/]"
        )

        if Confirm.ask("[bold]Wanna give it a whirl?[/]", default=False):
            try:
                console.print("[dim]Working my magic...[/dim]")
                result = _run_command(cmd, lang)
                console.print(f"[green]{choice(SUCCESS_MESSAGES)}[/green]")
                console.print(result.stdout)
            except subprocess.CalledProcessError as e:
                console.print(f"[red]{choice(emS)}[/red]")
                console.print(e.stderr)

                console.print("\n[bold]Asking my robot overlords for help...[/bold]")

                solution = _gs(cmd, e.stderr)

                dynamic_width = min(console.size.width - 4, 100)
                console.print(
                    Panel(
                        Markdown(solution),
                        title="[pink1]AI Rescue Squad[/]",
                        border_style="yellow",
                        box=ROUNDED,
                        padding=(1, 2),
                        width=dynamic_width,
                    )
                )

                new_commands = _extract_code_blocks(solution)
                if new_commands and Confirm.ask(
                    "[bold]Try the suggested fix?[/]", default=True
                ):
                    _ec(new_commands)


def _fr(response_text):
    """Process and format the API response"""
    cleaned_text = _cn(response_text)
    code_blocks = _extract_code_blocks(cleaned_text)

    panel_width = min(console.size.width - 4, 100)
    console.print(
        Panel(
            Markdown(cleaned_text),
            title="✨ Results ✨",
            border_style="green",
            box=ROUNDED,
            padding=(1, 2),
            width=panel_width,
        )
    )

    if code_blocks:
        _ec(code_blocks)


def _gr(prompt):
    try:
        _fr(_get_chat_session().send_message(prompt).text)
    except SystemExit as e:
        console.print(f"[red]{e}[/red]")
        raise
    except Exception as e:
        console.print("[red]My internet hamster fell off the wheel! 🐹[/red]")
        console.print(f"[dim]{type(e).__name__}: {e}[/dim]")


def chat_():
    """Friendly chat interface with humor"""
    console.print(
        Panel.fit(
            choice(WELCOME_MESSAGES),
            title="[bold purple]Magic Terminal Buddy[/]",
            border_style="purple",
            subtitle="Type 'exit' when you're done playing",
        )
    )

    while True:
        try:
            prompt = console.input("\n[bold cyan]Your wish: [/] ")
            if prompt.lower() in ("exit", "quit", "bye"):
                console.print("[yellow]Catch you on the flip side! 👋[/yellow]")
                break
            _gr(prompt)
        except KeyboardInterrupt:
            console.print("\n[yellow]Leaving so soon? Okay, bye! 👋[/yellow]")
            break


if __name__ == "__main__":
    chat_()
