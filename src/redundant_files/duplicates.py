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
        
    def find_duplicates(self, min_size: int = 0) -> list[DuplicateGroup]:
        """Find all duplicate groups. Returns sorted by wasted_space desc."""
        self.db.refresh_duplicate_groups()
        groups_data = self.db.get_duplicate_groups(min_size=min_size)
        
        groups: list[DuplicateGroup] = []
        for row in groups_data:
            full_hash = row["full_hash"]
            file_size = row["file_size"]
            
            files_data = self.db.get_group_files(full_hash)
            files = []
            for fdata in files_data:
                files.append(FileInfo(
                    file_id=fdata["id"],
                    volume_id=fdata["volume_id"],
                    volume_label=fdata.get("label", ""),
                    volume_serial=fdata.get("serial_number", ""),
                    last_drive_letter=fdata.get("last_drive_letter", ""),
                    relative_path=fdata["relative_path"],
                    file_size=fdata["file_size"],
                    full_hash=fdata["full_hash"],
                    file_modified_at=fdata["file_modified_at"]
                ))
            groups.append(DuplicateGroup(full_hash=full_hash, file_size=file_size, files=files))
            
        groups.sort(key=lambda g: g.wasted_space, reverse=True)
        return groups
    
    def get_summary(self, min_size: int = 0) -> DuplicateSummary:
        groups_data = self.db.get_duplicate_groups(min_size=min_size)
        total_groups = len(groups_data)
        total_dup_files = sum(row["file_count"] - 1 for row in groups_data)
        total_wasted = sum(row["file_size"] * (row["file_count"] - 1) for row in groups_data)
        
        return DuplicateSummary(
            total_groups=total_groups,
            total_duplicate_files=total_dup_files,
            total_wasted_space=total_wasted
        )
