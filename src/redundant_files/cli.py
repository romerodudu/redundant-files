from __future__ import annotations
import click
from rich.console import Console
from pathlib import Path

from .config import Config
from .database import Database
from .scanner import Scanner
from .duplicates import DuplicateDetector
from .cleanup import CleanupManager, CleanupPlan
from .scheduler import InsertionScheduler
from .ui import InteractiveUI
from .volume import wait_for_volume

def parse_size(size_str: str) -> int:
    """Parse human-readable size string to bytes. E.g., '10MB' -> 10485760."""
    size_str = size_str.upper().strip()
    if size_str == "0":
        return 0
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                num = float(size_str[:-len(suffix)].strip())
                return int(num * mult)
            except ValueError:
                pass
    try:
        return int(size_str)
    except ValueError:
        return 0

@click.group()
@click.option("--config", "-c", type=click.Path(exists=True), default=None,
              help="Path to config file")
@click.option("--db", type=click.Path(), default=None,
              help="Path to database file")
@click.pass_context
def cli(ctx, config, db):
    """Redundant Files — Duplicate file detector and manager."""
    console = Console()
    
    cfg = Config.load(Path(config) if config else None)
    if db:
        cfg.db_path = Path(db)
        
    database = Database(cfg.db_path)
    ctx.obj = {
        "config": cfg,
        "db": database,
        "console": console
    }

@cli.command()
@click.argument("path")
@click.option("--full", is_flag=True, help="Force full rehash of all files")
@click.option("--resume", is_flag=True, help="Resume an interrupted scan (also works without this flag)")
@click.pass_context
def scan(ctx, path, full, resume):
    """Scan a directory or volume for files.

    The scan can be interrupted with Ctrl+C at any time. Progress is saved
    automatically. Run the command again to resume where you left off —
    unchanged files are skipped.
    """
    db = ctx.obj["db"]
    cfg = ctx.obj["config"]
    console = ctx.obj["console"]
    
    scanner = Scanner(db, cfg)
    
    drive_letter = str(Path(path).anchor)[:2] if Path(path).is_absolute() else ""
    if not drive_letter:
        drive_letter = str(Path(path).resolve().anchor)[:2]
        
    console.print(f"[cyan]Scanning {path}...[/cyan]")
    res = scanner.scan(drive_letter=drive_letter, root_path=path, full=full, resume=resume)
    
    if res.interrupted:
        console.print(
            f"[yellow]Interrupted:[/yellow] {res.files_scanned} files seen, "
            f"{res.new_files} new, {res.updated_files} updated, "
            f"{res.skipped_files} skipped. Duration: {res.duration:.2f}s"
        )
    else:
        console.print(
            f"[green]Scan complete:[/green] {res.files_scanned} files scanned, "
            f"{res.new_files} new, {res.updated_files} updated, "
            f"{res.skipped_files} skipped, {res.deleted_files} deleted. "
            f"Duration: {res.duration:.2f}s"
        )

@cli.command()
@click.pass_context
def volumes(ctx):
    """List all known volumes."""
    db = ctx.obj["db"]
    console = ctx.obj["console"]
    ui = InteractiveUI(db, ctx.obj["config"], console)
    
    vols = db.list_volumes()
    from rich.table import Table
    table = Table(title="Volumes")
    table.add_column("ID")
    table.add_column("Label")
    table.add_column("Serial")
    table.add_column("Total Size")
    table.add_column("Last Drive")
    
    for v in vols:
        table.add_row(str(v["id"]), v["label"], v["serial_number"], 
                      ui.format_size(v["total_size"]), v["last_drive_letter"])
    console.print(table)

@cli.command()
@click.option("--min-size", default="0", help="Minimum file size (e.g., '10MB', '1GB')")
@click.pass_context
def duplicates(ctx, min_size):
    """Show duplicate file groups."""
    db = ctx.obj["db"]
    cfg = ctx.obj["config"]
    console = ctx.obj["console"]
    
    msize = parse_size(min_size)
    detector = DuplicateDetector(db)
    ui = InteractiveUI(db, cfg, console)
    
    groups = detector.find_duplicates(min_size=msize)
    ui.show_duplicates(groups)

@cli.command()
@click.option("--two-pass", is_flag=True, help="Use two-pass deletion (move first, delete later)")
@click.option("--pass2", is_flag=True, help="Execute pass 2: delete DUPLICATED/ folders")
@click.option("--min-size", default="0", help="Minimum file size filter")
@click.pass_context
def cleanup(ctx, two_pass, pass2, min_size):
    """Interactive duplicate cleanup."""
    db = ctx.obj["db"]
    cfg = ctx.obj["config"]
    console = ctx.obj["console"]
    
    cleanup_mgr = CleanupManager(db, cfg)
    scheduler = InsertionScheduler(db)
    ui = InteractiveUI(db, cfg, console)
    
    if pass2:
        pending = cleanup_mgr.get_pending_pass2_volumes()
        steps = scheduler.plan_pass2_insertions(pending)
        
        if not steps:
            console.print("[green]No pending pass 2 operations.[/green]")
            return
            
        ui.show_insertion_instructions(steps)
        for step in steps:
            drive = wait_for_volume(db, step.volume_id, step.volume_label, console)
            freed = cleanup_mgr.execute_pass2(step.volume_id, drive, console)
            console.print(f"Freed [green]{ui.format_size(freed)}[/green] on {step.volume_label}")
    else:
        msize = parse_size(min_size)
        detector = DuplicateDetector(db)
        groups = detector.find_duplicates(min_size=msize)
        
        if not groups:
            console.print("No duplicates found.")
            return
            
        plan = ui.select_files_to_keep(groups)
        if not ui.confirm_plan(plan):
            console.print("Cancelled.")
            return
            
        steps = scheduler.plan_insertions(plan)
        ui.show_insertion_instructions(steps)
        
        for step in steps:
            drive = wait_for_volume(db, step.volume_id, step.volume_label, console)
            vol_drives = {step.volume_id: drive}
            
            step_plan = CleanupPlan(actions=step.actions)
            
            if two_pass:
                cleanup_mgr.execute_pass1(step_plan, vol_drives, console)
            else:
                cleanup_mgr.execute_direct(step_plan, vol_drives, console)

@cli.command()
@click.pass_context
def status(ctx):
    """Show database status and statistics."""
    db = ctx.obj["db"]
    console = ctx.obj["console"]
    ui = InteractiveUI(db, ctx.obj["config"], console)
    
    vols = len(db.list_volumes())
    from rich.panel import Panel
    
    try:
        detector = DuplicateDetector(db)
        summary = detector.get_summary(min_size=0)
        
        stats = (
            f"Volumes: {vols}\n"
            f"Duplicate Groups: {summary.total_groups}\n"
            f"Duplicate Files: {summary.total_duplicate_files}\n"
            f"Total Wasted Space: {ui.format_size(summary.total_wasted_space)}"
        )
        console.print(Panel(stats, title="Database Status"))
    except Exception as e:
        console.print(f"[red]Error fetching status: {e}[/red]")

def main():
    cli()

if __name__ == "__main__":
    main()
