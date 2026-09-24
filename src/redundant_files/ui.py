from __future__ import annotations
from typing import TYPE_CHECKING
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich import box
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
        table.add_column("Files (Volume:Path)", style="blue")
        
        for i, group in enumerate(groups, 1):
            short_hash = group.full_hash[:8]
            size_str = self.format_size(group.file_size)
            wasted_str = self.format_size(group.wasted_space)
            
            files_lines = [f"{f.volume_label}:{f.relative_path}" for f in group.files]
            files_str = "\n".join(files_lines)
            
            table.add_row(str(i), short_hash, size_str, str(group.file_count), wasted_str, files_str)
            
        self.console.print(table)
        
    def select_files_to_keep(self, groups: list[DuplicateGroup]) -> CleanupPlan:
        """Interactive selection: for each group, choose which file to KEEP."""
        plan = CleanupPlan()
        
        for i, group in enumerate(groups, 1):
            self.console.print(f"\n[bold cyan]Group {i}/{len(groups)}[/bold cyan] - Size: [green]{self.format_size(group.file_size)}[/green]")
            for j, f in enumerate(group.files, 1):
                self.console.print(f"  [yellow]{j}.[/yellow] {f.volume_label}:{f.relative_path} (Mod: {f.file_modified_at})")
                
            choice = Prompt.ask(
                "Select file to [bold green]KEEP[/bold green] (number), 'skip' to ignore, 'all' for auto (keep oldest)", 
                default="all"
            )
            
            if choice.lower() == 'skip':
                continue
                
            keep_idx = -1
            if choice.lower() == 'all':
                # Auto: keep oldest modified file
                oldest_file = min(group.files, key=lambda f: f.file_modified_at)
                keep_idx = group.files.index(oldest_file)
            else:
                try:
                    keep_idx = int(choice) - 1
                    if not (0 <= keep_idx < len(group.files)):
                        self.console.print("[red]Invalid index, skipping group.[/red]")
                        continue
                except ValueError:
                    self.console.print("[red]Invalid input, skipping group.[/red]")
                    continue
                    
            f_keep_orig = group.files[keep_idx]
            f_keep = FileToKeep(
                file_id=f_keep_orig.file_id,
                volume_id=f_keep_orig.volume_id,
                volume_label=f_keep_orig.volume_label,
                relative_path=f_keep_orig.relative_path
            )
            
            for j, f in enumerate(group.files):
                if j != keep_idx:
                    f_del = FileToDelete(
                        file_id=f.file_id,
                        volume_id=f.volume_id,
                        volume_label=f.volume_label,
                        relative_path=f.relative_path,
                        file_size=f.file_size
                    )
                    plan.actions.append(CleanupAction(
                        file_to_delete=f_del,
                        file_to_keep=f_keep,
                        full_hash=group.full_hash
                    ))
                    
        return plan

    def confirm_plan(self, plan: CleanupPlan) -> bool:
        """Show summary of plan and ask for confirmation."""
        if not plan.actions:
            self.console.print("[yellow]No actions in plan.[/yellow]")
            return False
            
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
