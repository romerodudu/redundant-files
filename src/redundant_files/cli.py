from __future__ import annotations
import click
from rich.console import Console
from pathlib import Path
import questionary
from rich.prompt import Confirm
from rich.table import Table
from rich.panel import Panel

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
    ctx.call_on_close(database.close)
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
        
    ui = InteractiveUI(db, cfg, console)
    cfg_src = getattr(cfg, 'config_source', None)
    cfg_display = str(cfg_src) if cfg_src else "defaults"
    min_size_display = ui.format_size(cfg.min_file_size)
    console.print(f"[cyan]Scanning {path}...[/cyan] [dim](Config: {cfg_display} | Min size: {min_size_display})[/dim]")
    res = scanner.scan(drive_letter=drive_letter, root_path=path, full=full, resume=resume)
    
    unique_size_files = getattr(res, 'unique_size_files', 0)
    
    if res.interrupted:
        console.print(
            f"[yellow]Interrupted:[/yellow] {res.files_scanned} files seen, "
            f"{res.new_files} new, {res.updated_files} updated, "
            f"{res.skipped_files} skipped, {unique_size_files} unique-size. Duration: {res.duration:.2f}s"
        )
    else:
        console.print(
            f"[green]Scan complete:[/green] {res.files_scanned} files scanned, "
            f"{res.new_files} new, {res.updated_files} updated, "
            f"{res.skipped_files} skipped, {res.deleted_files} deleted, {unique_size_files} unique-size. "
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
@click.argument("volume_id", type=int)
@click.option("--remove-volume", is_flag=True, help="Also remove the volume record itself")
@click.pass_context
def reset(ctx, volume_id, remove_volume):
    """Clear scanned data for a volume from the database. Files on disk are NOT affected."""
    db = ctx.obj["db"]
    console = ctx.obj["console"]
    
    volume = db.get_volume(volume_id)
    if not volume:
        console.print(f"[red]Volume ID {volume_id} not found.[/red]")
        return
        
    console.print(f"Volume: {volume['label']} (Serial: {volume['serial_number']})")
    if not Confirm.ask("Are you sure you want to reset this volume's data in the database?"):
        console.print("Cancelled.")
        return
        
    f_cleared, s_cleared = db.reset_volume(volume_id, remove_volume=remove_volume)
    console.print(f"[green]Reset complete.[/green] Cleared {f_cleared} files and {s_cleared} scans.")
    if remove_volume:
        console.print("Volume record removed.")

@cli.command()
@click.option("--clear", is_flag=True, help="Remove all ignored groups")
@click.option("--manage", is_flag=True, help="Interactive menu to un-ignore groups")
@click.pass_context
def ignored(ctx, clear, manage):
    """Manage ignored duplicate groups."""
    db = ctx.obj["db"]
    console = ctx.obj["console"]
    ui = InteractiveUI(db, ctx.obj["config"], console)
    
    if clear:
        if Confirm.ask("Are you sure you want to clear ALL ignored groups?"):
            removed = db.clear_ignored_groups()
            console.print(f"[green]Cleared {removed} ignored groups.[/green]")
        return
        
    groups = db.list_ignored_groups()
    if not groups:
        console.print("No ignored groups.")
        return
        
    if manage:
        choices = [
            questionary.Choice(f"Hash: {g['full_hash'][:8]} (Files: {g['file_count']}, Size: {ui.format_size(g['file_size'])})", value=g['full_hash'])
            for g in groups
        ]
        selected = questionary.checkbox("Select groups to UN-IGNORE:", choices=choices).ask()
        
        if selected:
            for h in selected:
                db.unignore_group(h)
            console.print(f"[green]Un-ignored {len(selected)} groups.[/green]")
        return
        
    table = Table(title="Ignored Groups")
    table.add_column("Hash (short)")
    table.add_column("Files")
    table.add_column("Size")
    
    for g in groups:
        table.add_row(g["full_hash"][:8], str(g["file_count"]), ui.format_size(g["file_size"]))
        
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

    pending = db.get_pending_cross_volume_hashes()
    if pending:
        by_vol = {}
        for f in pending:
            letter = (f.get('last_drive_letter') or "").rstrip("\\")
            lbl = (f.get('volume_label') or "").strip()
            if lbl and letter:
                v_name = f"{lbl} ({letter})"
            elif lbl:
                v_name = lbl
            elif letter:
                v_name = f"Drive {letter}"
            else:
                v_name = f"Volume {f['volume_id']}"
            by_vol.setdefault(v_name, []).append(f)
        console.print(f"\n[yellow]Notice: {len(pending)} candidate duplicate files on other volumes need full hash verification:[/yellow]")
        for v_name, files_list in by_vol.items():
            console.print(f"  [yellow]• {v_name}[/yellow]: {len(files_list)} files waiting. (Insert volume and scan to verify)")

@cli.command()
@click.option("--two-pass", is_flag=True, help="Use two-pass deletion (move first, delete later)")
@click.option("--pass2", is_flag=True, help="Execute pass 2: delete DUPLICATED/ folders")
@click.option("--min-size", default="0", help="Minimum file size filter")
@click.option("--include-ignored", is_flag=True, help="Include previously ignored groups")
@click.pass_context
def cleanup(ctx, two_pass, pass2, min_size, include_ignored):
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
        groups = detector.find_duplicates(min_size=msize, exclude_ignored=not include_ignored)
        
        if not groups:
            console.print("No duplicates found.")
            return
            
        plan, ignored_hashes = ui.select_files_to_delete(groups)
        
        for h in ignored_hashes:
            g_info = next((g for g in groups if g.full_hash == h), None)
            if g_info:
                db.ignore_group(h, file_count=g_info.file_count, file_size=g_info.file_size)
            else:
                db.ignore_group(h)
                
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
    
    try:
        detector = DuplicateDetector(db)
        summary = detector.get_summary(min_size=0)
        
        cfg = ctx.obj["config"]
        cfg_src = getattr(cfg, 'config_source', None) or "defaults"
        ex_dirs = ', '.join(cfg.exclude_dirs) if cfg.exclude_dirs else 'none'
        ex_pats = ', '.join(cfg.exclude_patterns) if cfg.exclude_patterns else 'none'
        ex_exts = ', '.join(cfg.exclude_extensions) if cfg.exclude_extensions else 'none'

        pending = db.get_pending_cross_volume_hashes()
        pending_line = f"\nPending Verifications (other volumes): {len(pending)}" if pending else ""

        stats = (
            f"Config file: {cfg_src}\n"
            f"Database: {cfg.db_path}\n"
            f"Min file size: {ui.format_size(cfg.min_file_size)} ({cfg.min_file_size:,} bytes)\n"
            f"Excluded dirs: {ex_dirs}\n"
            f"Excluded patterns: {ex_pats}\n"
            f"Excluded extensions: {ex_exts}\n"
            f"----------------------------------------\n"
            f"Volumes: {vols}\n"
            f"Duplicate Groups: {summary.total_groups}\n"
            f"Duplicate Files: {summary.total_duplicate_files}\n"
            f"Total Wasted Space: {ui.format_size(summary.total_wasted_space)}"
            f"{pending_line}"
        )
        console.print(Panel(stats, title="Database & Configuration Status"))
    except Exception as e:
        console.print(f"[red]Error fetching status: {e}[/red]")

def main():
    cli()

if __name__ == "__main__":
    main()
