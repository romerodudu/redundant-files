from __future__ import annotations
from typing import TYPE_CHECKING
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich import box
import questionary
from .cleanup import CleanupPlan, CleanupAction, FileToDelete, FileToKeep

if TYPE_CHECKING:
    from .database import Database
    from .config import Config
    from .duplicates import DuplicateGroup
    from .scheduler import InsertionStep

class InteractiveUI:
    def __init__(self, db: Database, config: Config, console: Console):
        self.db = db
        self.config = config
        self.console = console
        
    def format_size(self, size_bytes: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:3.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:3.1f} PB"

    def show_duplicates(self, groups: list[DuplicateGroup]):
        """Display duplicate groups in a rich table."""
        table = Table(title="Duplicate Files", box=box.ROUNDED)
        table.add_column("Group #", justify="right", style="cyan", no_wrap=True)
        table.add_column("Hash (short)", style="magenta")
        table.add_column("Size", justify="right", style="green")
        table.add_column("Count", justify="right", style="yellow")
        table.add_column("Wasted", justify="right", style="red")
        table.add_column("Files (Volume | Path)", style="blue")
        
        for i, group in enumerate(groups, 1):
            short_hash = group.full_hash[:8]
            size_str = self.format_size(group.file_size)
            wasted_str = self.format_size(group.wasted_space)
            
            files_lines = [f"[{f.volume_display_name}] {f.full_path}" for f in group.files]
            files_str = "\n".join(files_lines)
            
            table.add_row(str(i), short_hash, size_str, str(group.file_count), wasted_str, files_str)
            
        self.console.print(table)
        
    def select_files_to_delete(self, groups: list[DuplicateGroup]) -> tuple[CleanupPlan, list[str]]:
        """Interactive selection: uses ncurses full-screen TUI (with fallback to questionary)."""
        import sys
        
        # Try full-screen curses TUI if interactive terminal
        if sys.stdin.isatty():
            try:
                from .tui_cleanup import run_cleanup_tui
                return run_cleanup_tui(groups)
            except Exception as e:
                self.console.print(f"[yellow]Note: Ncurses TUI unavailable ({e}), using interactive prompts.[/yellow]")

        plan = CleanupPlan()
        ignored_hashes = []
        
        for i, group in enumerate(groups, 1):
            self.console.print(f"\n[bold cyan]Group {i}/{len(groups)}[/bold cyan] - Size: [green]{self.format_size(group.file_size)}[/green]")
            short_hash = group.full_hash[:8]
            self.console.print(f"Hash: {short_hash} | Count: {group.file_count}")
            
            action = questionary.select(
                "What would you like to do with this group?",
                choices=[
                    'Select files to delete',
                    'Skip this group',
                    'Ignore this group (hide in future)',
                    'Auto (keep oldest)'
                ]
            ).ask()
            
            if action is None or action == 'Skip this group':
                continue
                
            if action == 'Ignore this group (hide in future)':
                ignored_hashes.append(group.full_hash)
                continue
                
            files_to_delete = []
            if action == 'Auto (keep oldest)':
                oldest_file = min(group.files, key=lambda f: f.file_modified_at)
                files_to_delete = [f for f in group.files if f != oldest_file]
            elif action == 'Select files to delete':
                while True:
                    choices = [
                        questionary.Choice(f"[{f.volume_display_name}] {f.full_path} ({self.format_size(f.file_size)})", value=f)
                        for f in group.files
                    ]
                    selected = questionary.checkbox(
                        "Select files to DELETE:",
                        choices=choices
                    ).ask()
                    
                    if selected is None:
                        break
                        
                    if len(selected) == len(group.files):
                        self.console.print("[red]You cannot delete ALL files in the group. Please keep at least one.[/red]")
                        continue
                        
                    files_to_delete = selected
                    break
                    
            if not files_to_delete:
                continue
                
            keep_file = next(f for f in group.files if f not in files_to_delete)
            f_keep = FileToKeep(
                file_id=keep_file.file_id,
                volume_id=keep_file.volume_id,
                volume_label=keep_file.volume_label,
                relative_path=keep_file.relative_path,
                last_drive_letter=keep_file.last_drive_letter
            )
            
            for f in files_to_delete:
                f_del = FileToDelete(
                    file_id=f.file_id,
                    volume_id=f.volume_id,
                    volume_label=f.volume_label,
                    relative_path=f.relative_path,
                    file_size=f.file_size,
                    last_drive_letter=f.last_drive_letter
                )
                plan.actions.append(CleanupAction(
                    file_to_delete=f_del,
                    file_to_keep=f_keep,
                    full_hash=group.full_hash
                ))
                
        return plan, ignored_hashes

    def confirm_plan(self, plan: CleanupPlan) -> bool:
        """Show list of selected files, summary of plan, and ask for confirmation."""
        if not plan.actions:
            self.console.print("[yellow]No actions in plan.[/yellow]")
            return False

        # Table of files selected for deletion
        table = Table(title="Files Selected for Deletion", box=box.ROUNDED)
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("File to Delete (Volume | Path)", style="red")
        table.add_column("Size", justify="right", style="green")
        table.add_column("Kept Copy (Volume | Path)", style="dim green")

        for idx, action in enumerate(plan.actions, 1):
            f_del = action.file_to_delete
            f_keep = action.file_to_keep
            table.add_row(
                str(idx),
                f"[{f_del.volume_display_name}] {f_del.full_path}",
                self.format_size(f_del.file_size),
                f"[{f_keep.volume_display_name}] {f_keep.full_path}",
            )

        self.console.print(table)

        summary = (
            f"Files to delete: [bold red]{len(plan.actions)}[/bold red]\n"
            f"Space to save: [bold green]{self.format_size(plan.total_space_saved)}[/bold green]\n"
            f"Volumes involved: [bold cyan]{len(plan.volumes_involved)}[/bold cyan]"
        )
        panel = Panel(summary, title="Cleanup Plan Summary", box=box.ROUNDED)
        self.console.print(panel)
        
        return Confirm.ask("Execute this cleanup plan?")

    def show_insertion_instructions(self, steps: list[InsertionStep]):
        """Show the ordered list of volume insertions needed."""
        if not steps:
            return
            
        self.console.print("\n[bold]Volume Insertion Steps:[/bold]")
        for i, step in enumerate(steps, 1):
            self.console.print(f"  [yellow]{i}.[/yellow] Insert volume: [bold cyan]{step.volume_label}[/bold cyan] "
                               f"(Actions: {len(step.actions)}, Space: {self.format_size(step.space_freed)})")
