"""Chat-editor REPL.

Usage:
    uv run python -m chat.cli <video.mp4>

Type natural-language commands in Turkish or English:
    "bu videodan 3 klip çıkar", "2. klibe enerjik müzik ekle",
    "altyazıları büyüt", "5. saniyeye zoom ekle", "klibi göster", "geri al"
Escape commands: /state /undo /quit
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel

from chat.agent import run_turn
from chat.session import Session


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    console = Console()
    session = Session.load_or_create(sys.argv[1])
    history: list[dict] = []

    console.print(Panel.fit(
        f"[bold]shorts-mcp chat editor[/bold]\n{session.summary()}",
        border_style="cyan"))
    console.print("[dim]Komut yaz (TR/EN). /state /undo /quit[/dim]\n")

    while True:
        try:
            user = console.input("[bold green]sen >[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user in ("/quit", "/q", "exit"):
            break
        if user == "/state":
            console.print(Panel(session.summary(), border_style="cyan"))
            continue
        if user == "/undo":
            console.print(session.undo())
            continue

        def on_tool(name: str, args: dict) -> None:
            console.print(f"[dim]  ⚙ {name}({args})[/dim]")

        with console.status("[cyan]düşünüyor / işliyor...[/cyan]"):
            try:
                reply = run_turn(session, history, user, on_tool=on_tool)
            except Exception as e:
                reply = f"Hata: {type(e).__name__}: {e}"
        console.print(f"[bold cyan]editör >[/bold cyan] {reply}\n")

    session.save()
    console.print("[dim]Oturum kaydedildi.[/dim]")


if __name__ == "__main__":
    main()
