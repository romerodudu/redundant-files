from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database

@dataclass
class FileInfo:
    file_id: int
    volume_id: int
    volume_label: str
    volume_serial: str
    last_drive_letter: str
    relative_path: str
    file_size: int
    full_hash: str
    file_modified_at: float

    @property
    def volume_display_name(self) -> str:
        """Returns a clear volume tag, e.g. 'Elements (K:)' or 'Drive E:' or 'Elements'."""
        letter = (self.last_drive_letter or "").rstrip("\\")
        lbl = (self.volume_label or "").strip()
        if lbl and letter:
            return f"{lbl} ({letter})"
        elif lbl:
            return lbl
        elif letter:
            return f"Drive {letter}"
        elif self.volume_serial:
            return f"Vol [{self.volume_serial}]"
        else:
            return f"Vol #{self.volume_id}"

    @property
    def full_path(self) -> str:
        """Returns full absolute path if drive letter is known, otherwise volume:relative_path."""
        letter = (self.last_drive_letter or "").rstrip("\\")
        if letter:
            clean_rel = self.relative_path.lstrip("\\/")
            return f"{letter}\\{clean_rel}"
        lbl = self.volume_display_name
        return f"{lbl}:{self.relative_path}"

@dataclass
class DuplicateGroup:
    full_hash: str
    file_size: int
    files: list[FileInfo]
    
    @property
    def wasted_space(self) -> int:
        return self.file_size * (len(self.files) - 1)
    
    @property
    def file_count(self) -> int:
        return len(self.files)

@dataclass
class DuplicateSummary:
    total_groups: int
    total_duplicate_files: int
    total_wasted_space: int

class DuplicateDetector:
    def __init__(self, db: Database):
        self.db = db
        
    def find_duplicates(self, min_size: int = 0, exclude_ignored: bool = True) -> list[DuplicateGroup]:
        """Find all duplicate groups. Returns sorted by wasted_space desc."""
        self.db.refresh_duplicate_groups()
        groups_data = self.db.get_duplicate_groups(min_size=min_size)
        
        groups: list[DuplicateGroup] = []
        for row in groups_data:
            full_hash = row["full_hash"]
            if exclude_ignored and self.db.is_group_ignored(full_hash):
                continue
            
            file_size = row["file_size"]
            
            files_data = self.db.get_group_files(full_hash)
            files = []
            for fdata in files_data:
                vol_label = fdata.get("volume_label") or fdata.get("label") or ""
                vol_serial = fdata.get("volume_serial") or fdata.get("serial_number") or ""
                files.append(FileInfo(
                    file_id=fdata["id"],
                    volume_id=fdata["volume_id"],
                    volume_label=vol_label,
                    volume_serial=vol_serial,
                    last_drive_letter=fdata.get("last_drive_letter") or "",
                    relative_path=fdata["relative_path"],
                    file_size=fdata["file_size"],
                    full_hash=fdata["full_hash"],
                    file_modified_at=fdata["file_modified_at"]
                ))
            groups.append(DuplicateGroup(full_hash=full_hash, file_size=file_size, files=files))
            
        groups.sort(key=lambda g: g.wasted_space, reverse=True)
        return groups
    
    def get_summary(self, min_size: int = 0, exclude_ignored: bool = True) -> DuplicateSummary:
        groups_data = self.db.get_duplicate_groups(min_size=min_size)
        if exclude_ignored:
            groups_data = [row for row in groups_data if not self.db.is_group_ignored(row["full_hash"])]
            
        total_groups = len(groups_data)
        total_dup_files = sum(row["file_count"] - 1 for row in groups_data)
        total_wasted = sum(row["file_size"] * (row["file_count"] - 1) for row in groups_data)
        
        return DuplicateSummary(
            total_groups=total_groups,
            total_duplicate_files=total_dup_files,
            total_wasted_space=total_wasted
        )
