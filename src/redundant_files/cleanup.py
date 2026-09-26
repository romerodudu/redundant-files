from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
import os
import json
from typing import TYPE_CHECKING
from datetime import datetime, timezone

if TYPE_CHECKING:
    from .database import Database
    from .config import Config
    from rich.console import Console

@dataclass
class FileToDelete:
    file_id: int
    volume_id: int
    volume_label: str
    relative_path: str
    file_size: int
    last_drive_letter: str = ""

    @property
    def volume_display_name(self) -> str:
        letter = (self.last_drive_letter or "").rstrip("\\")
        lbl = (self.volume_label or "").strip()
        if lbl and letter:
            return f"{lbl} ({letter})"
        elif lbl:
            return lbl
        elif letter:
            return f"Drive {letter}"
        else:
            return f"Volume {self.volume_id}"

    @property
    def full_path(self) -> str:
        letter = (self.last_drive_letter or "").rstrip("\\")
        if letter:
            clean_rel = self.relative_path.lstrip("\\/")
            return f"{letter}\\{clean_rel}"
        lbl = self.volume_display_name
        return f"{lbl}:{self.relative_path}"

@dataclass
class FileToKeep:
    file_id: int
    volume_id: int
    volume_label: str
    relative_path: str
    last_drive_letter: str = ""

    @property
    def volume_display_name(self) -> str:
        letter = (self.last_drive_letter or "").rstrip("\\")
        lbl = (self.volume_label or "").strip()
        if lbl and letter:
            return f"{lbl} ({letter})"
        elif lbl:
            return lbl
        elif letter:
            return f"Drive {letter}"
        else:
            return f"Volume {self.volume_id}"

    @property
    def full_path(self) -> str:
        letter = (self.last_drive_letter or "").rstrip("\\")
        if letter:
            clean_rel = self.relative_path.lstrip("\\/")
            return f"{letter}\\{clean_rel}"
        lbl = self.volume_display_name
        return f"{lbl}:{self.relative_path}"

@dataclass
class CleanupAction:
    file_to_delete: FileToDelete
    file_to_keep: FileToKeep
    full_hash: str

@dataclass
class CleanupPlan:
    actions: list[CleanupAction] = field(default_factory=list)
    
    @property
    def total_space_saved(self) -> int:
        return sum(a.file_to_delete.file_size for a in self.actions)
    
    @property
    def volumes_involved(self) -> set[int]:
        return {a.file_to_delete.volume_id for a in self.actions}
    
    def actions_by_volume(self) -> dict[int, list[CleanupAction]]:
        result: dict[int, list[CleanupAction]] = {}
        for action in self.actions:
            vid = action.file_to_delete.volume_id
            result.setdefault(vid, []).append(action)
        return result

DUPLICATED_DIR = "DUPLICATED"

class CleanupManager:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
    
    def execute_pass1(self, plan: CleanupPlan, volume_drive_letters: dict[int, str], console: Console):
        """Pass 1: Move duplicates to DUPLICATED/ folder with .info.txt files."""
        for volume_id, actions in plan.actions_by_volume().items():
            if volume_id not in volume_drive_letters:
                console.print(f"[yellow]Skipping volume {volume_id}, not connected.[/yellow]")
                continue
                
            drive = Path(volume_drive_letters[volume_id])
            for action in actions:
                f_delete = action.file_to_delete
                f_keep = action.file_to_keep
                
                src = drive / f_delete.relative_path
                dst = drive / DUPLICATED_DIR / f_delete.relative_path
                
                if not src.exists():
                    console.print(f"[yellow]File not found: {src}[/yellow]")
                    continue
                
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                
                info_path = dst.with_suffix(dst.suffix + ".info.txt")
                with info_path.open("w", encoding="utf-8") as f:
                    f.write(f"Original path: [{f_delete.volume_display_name}] {f_delete.full_path}\n")
                    f.write(f"Kept copy: [{f_keep.volume_display_name}] {f_keep.full_path}\n")
                    f.write(f"Hash: blake3:{action.full_hash}\n")
                    f.write(f"Moved at: {datetime.now(timezone.utc).isoformat()}\n")
                    
                self.db.mark_file_deleted(f_delete.file_id)
                console.print(f"[green]Moved: [{f_delete.volume_display_name}] {f_delete.relative_path}[/green]")

    def execute_pass2(self, volume_id: int, drive_letter: str, console: Console) -> int:
        """Pass 2: Delete DUPLICATED/ folder on the given volume. Returns bytes freed."""
        dup_dir = Path(drive_letter) / DUPLICATED_DIR
        if not dup_dir.exists():
            return 0
            
        bytes_freed = 0
        for root, dirs, files in os.walk(str(dup_dir)):
            for file in files:
                filepath = Path(root) / file
                try:
                    bytes_freed += filepath.stat().st_size
                except OSError:
                    pass
                    
        shutil.rmtree(str(dup_dir), ignore_errors=True)
        console.print(f"[green]Deleted {dup_dir}[/green]")
        return bytes_freed
    
    def execute_direct(self, plan: CleanupPlan, volume_drive_letters: dict[int, str], console: Console):
        """Direct delete without intermediate DUPLICATED/ folder."""
        for volume_id, actions in plan.actions_by_volume().items():
            if volume_id not in volume_drive_letters:
                console.print(f"[yellow]Skipping volume {volume_id}, not connected.[/yellow]")
                continue
                
            drive = Path(volume_drive_letters[volume_id])
            for action in actions:
                f_delete = action.file_to_delete
                src = drive / f_delete.relative_path
                
                if src.exists():
                    try:
                        src.unlink()
                        console.print(f"[green]Deleted: [{f_delete.volume_display_name}] {f_delete.relative_path}[/green]")
                    except OSError as e:
                        console.print(f"[red]Error deleting {src}: {e}[/red]")
                        continue
                
                self.db.mark_file_deleted(f_delete.file_id)

    def get_pending_pass2_volumes(self) -> list[dict]:
        """Check which volumes have DUPLICATED/ folders pending cleanup."""
        from .volume import detect_removable_drives, get_volume_info, identify_volume
        
        drives = detect_removable_drives()
        connected_drives = list(drives)
        for dl in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{dl}:\\"
            if drive not in connected_drives and Path(drive).exists():
                connected_drives.append(drive)
                
        pending = []
        for drive in connected_drives:
            dup_dir = Path(drive) / DUPLICATED_DIR
            if dup_dir.exists():
                try:
                    vol_info = get_volume_info(drive)
                    vol_dict = self.db.get_volume_by_serial(vol_info.serial_number)
                    if vol_dict:
                        vol_dict["current_drive"] = drive
                        pending.append(vol_dict)
                except Exception:
                    pass
        return pending
