import subprocess
import re
import platform
import time
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.box import ROUNDED
from random import choice
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
import os
from getpass import getpass

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def _parse_fallback_models() -> list[str]:
    raw = (os.environ.get("GEMINI_MODEL_FALLBACKS") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]

    # Conservative defaults; actual availability depends on your project/quota.
    return [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]


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


_client = None
_chat_session = None
_model_name = DEFAULT_MODEL
_model_notice = None
_last_fallback_attempts: list[str] = []


def _get_chat_session():
    global _client, _chat_session
    if _chat_session is not None:
        return _chat_session

    api_key = _get_api_key()
    if _client is None:
        _client = genai.Client(api_key=api_key)

    _chat_session = _client.chats.create(
        model=_model_name,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return _chat_session


def _reset_chat_session():
    global _client, _chat_session
    _chat_session = None
    _client = None


def _set_model_name(model_name: str, reason: str | None = None):
    global _model_name, _model_notice
    model_name = (model_name or "").strip()
    if not model_name:
        return
    if model_name == _model_name:
        return
    _model_name = model_name
    _model_notice = f"Switched model to '{_model_name}'" + (
        f" ({reason})" if reason else ""
    )


def _send_message(text: str):
    """Send a message with one automatic retry for closed-client errors."""
    try:
        return _get_chat_session().send_message(text)
    except RuntimeError as e:
        msg = str(e).lower()
        if "client has been closed" in msg or "cannot send a request" in msg:
            _reset_chat_session()
            return _get_chat_session().send_message(text)
        raise
    except genai_errors.ClientError as e:
        # If the configured model has quota=0, try a fallback model automatically.
        lower = str(e).lower()
        if "resource_exhausted" in lower and "limit: 0" in lower:
            current = _model_name
            global _last_fallback_attempts
            _last_fallback_attempts = [current]
            for candidate in _parse_fallback_models():
                if candidate == current:
                    continue
                try:
                    _last_fallback_attempts.append(candidate)
                    _set_model_name(candidate, reason="fallback")
                    _reset_chat_session()
                    return _get_chat_session().send_message(text)
                except genai_errors.ClientError:
                    continue
            # Restore original model if all fallbacks fail.
            _set_model_name(current)
            _reset_chat_session()
        raise


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
        return _send_message(prompt).text
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
        global _model_notice
        if _model_notice:
            console.print(f"[yellow]{_model_notice}[/yellow]")
            _model_notice = None

        _fr(_send_message(prompt).text)
    except SystemExit as e:
        console.print(f"[red]{e}[/red]")
        raise
    except genai_errors.ClientError as e:
        msg = str(e)
        lower = msg.lower()
        if "resource_exhausted" in lower or "quota" in lower or "rate" in lower:
            if "limit: 0" in lower or "limit: 0," in lower:
                console.print(
                    "[red]Gemini quota appears to be 0 for this model/key.[/red]"
                )
                if _last_fallback_attempts:
                    console.print(
                        "[dim]Tried models: "
                        + ", ".join(_last_fallback_attempts)
                        + "[/dim]"
                    )
                console.print(
                    "[dim]Fix: check billing/quota, or set a different model via $env:GEMINI_MODEL='...'.[/dim]"
                )
                console.print(
                    "[dim]Optional: set fallbacks via $env:GEMINI_MODEL_FALLBACKS='model1,model2,...'[/dim]"
                )
                console.print(f"[dim]{msg}[/dim]")
                return

            m = re.search(r"retry in\s+([0-9.]+)s", lower)
            if m:
                delay = float(m.group(1))
                delay = min(max(delay, 1.0), 30.0)
                console.print(
                    f"[yellow]Rate limited. Waiting {delay:.1f}s then retrying once...[/yellow]"
                )
                time.sleep(delay)
                _fr(_send_message(prompt).text)
                return

        console.print("[red]Gemini API error[/red]")
        console.print(f"[dim]{msg}[/dim]")
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
