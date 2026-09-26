import os
import signal
import mmap
import re
import time
import fnmatch
from dataclasses import dataclass
from pathlib import Path
import blake3
from collections import defaultdict
from typing import Generator, TYPE_CHECKING, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, TaskProgressColumn, DownloadColumn,
    TransferSpeedColumn, TimeRemainingColumn
)
from rich.console import Console

if TYPE_CHECKING:
    from .database import Database
    from .config import Config

@dataclass
class _FileEntry:
    filepath: Path
    rel_path: str
    size: int
    mtime: float

@dataclass
class ScanResult:
    volume_id: int
    scan_id: int
    files_scanned: int
    new_files: int
    updated_files: int
    skipped_files: int
    deleted_files: int
    unique_size_files: int
    duration: float
    interrupted: bool


class Scanner:
    def __init__(self, db: "Database", config: "Config"):
        self.db = db
        self.config = config
        self._interrupted = False

        # Optimization #6: Pre-compiled exclusion patterns (case-insensitive)
        self._exclude_extensions = frozenset(
            e.lstrip('.').lower() for e in config.exclude_extensions
        )
        
        # Split exact directory names vs globs
        self._exclude_dirs_exact = frozenset(
            d.lower().strip('/\\').replace('\\', '/')
            for d in config.exclude_dirs if not any(c in d for c in '*?[')
        )
        dir_patterns = [d.replace('\\', '/') for d in config.exclude_dirs if any(c in d for c in '*?[')]
        self._exclude_dirs_regex = [re.compile(fnmatch.translate(p), re.IGNORECASE) for p in dir_patterns]
        
        self._exclude_patterns_regex = [
            re.compile(fnmatch.translate(p.replace('\\', '/')), re.IGNORECASE)
            for p in config.exclude_patterns
        ]

    def _handle_interrupt(self, signum, frame):
        """Handle Ctrl+C gracefully by setting interrupted flag."""
        self._interrupted = True

    def _should_exclude_name(self, name: str, rel_path: str = "", is_dir: bool = False) -> bool:
        name_lower = name.lower()
        rel_norm = rel_path.lower().replace('\\', '/') if rel_path else ""

        if is_dir:
            if name_lower in self._exclude_dirs_exact:
                return True
            if rel_norm and rel_norm in self._exclude_dirs_exact:
                return True
            for r in self._exclude_dirs_regex:
                if r.match(name) or (rel_norm and r.match(rel_norm)):
                    return True
            return False

        ext = name_lower.rsplit('.', 1)[-1] if '.' in name_lower else ''
        if ext in self._exclude_extensions:
            return True

        for r in self._exclude_patterns_regex:
            if r.match(name) or (rel_norm and r.match(rel_norm)):
                return True
        return False

    def scan(self, drive_letter: str, root_path: Optional[str] = None,
             full: bool = False, resume: bool = False) -> ScanResult:
        """Scan a volume/directory."""
        from .volume import identify_volume

        self._interrupted = False
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_interrupt)
        console = Console()

        try:
            start_time = time.time()

            volume_id, vol_info = identify_volume(self.db, drive_letter)

            scan_root = root_path if root_path else vol_info.drive_letter
            last_scan = self.db.get_last_scan(volume_id)
            if last_scan and last_scan.get('status') in ('interrupted', 'running'):
                console.print(
                    f"[yellow]Previous scan was interrupted "
                    f"({last_scan.get('files_scanned', '?')} files processed). "
                    f"Resuming — unchanged files will be skipped automatically.[/yellow]"
                )

            scan_id = self.db.create_scan(volume_id, scan_root)

            files_scanned = 0
            new_files = 0
            updated_files = 0
            skipped_files = 0
            unique_size_files = 0
            deleted_files = 0

            existing_db_files = {
                f['relative_path']: f
                for f in self.db.get_files_by_volume(volume_id)
            }
            current_disk_files: set[str] = set()
            scan_root_path = Path(scan_root)
            
            entries: list[_FileEntry] = []

            # Phase 1/4: Enumerating files...
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                transient=True,
            ) as progress:
                task = progress.add_task("[cyan]Phase 1/4: Enumerating files...", total=None)

                for filepath, size, mtime in self._enumerate_files(scan_root_path):
                    if self._interrupted:
                        progress.console.print(
                            "\n[yellow bold]⚠ Scan interrupted by user. "
                            "Progress has been saved.[/yellow bold]\n"
                            "[dim]Run the scan command again to resume "
                            "where you left off.[/dim]"
                        )
                        break

                    try:
                        try:
                            rel_path = str(filepath.relative_to(vol_info.drive_letter))
                        except ValueError:
                            rel_path = str(filepath.relative_to(scan_root_path))
                    except ValueError:
                        continue
                        
                    entries.append(_FileEntry(filepath, rel_path, size, mtime))
                    files_scanned += 1
                    progress.update(task, advance=1)

            # Phase 2/4: Checking for changes...
            needs_processing: list[_FileEntry] = []
            if not self._interrupted:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeElapsedColumn(),
                    transient=True,
                ) as progress:
                    task = progress.add_task("[cyan]Phase 2/4: Checking for changes...", total=len(entries))
                    
                    for entry in entries:
                        if self._interrupted:
                            break
                        
                        current_disk_files.add(entry.rel_path)
                        db_file = existing_db_files.get(entry.rel_path)
                        
                        if not full and db_file and not self._needs_rescan(db_file, entry.mtime):
                            skipped_files += 1
                        else:
                            needs_processing.append(entry)
                            
                        progress.update(task, advance=1)

            # Phase 3/4: Hashing (size pre-filter)...
            if not self._interrupted and needs_processing:
                # Optimization #1: Size pre-grouping
                size_groups = defaultdict(list)
                for entry in needs_processing:
                    size_groups[entry.size].append(entry)
                    
                # Optimization #7: Cross-volume size pre-filter
                db_size_counts = self.db.get_size_counts(set(size_groups.keys()))

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeElapsedColumn(),
                    transient=True,
                ) as progress:
                    task = progress.add_task("[cyan]Phase 3/4: Hashing (size pre-filter)...", total=len(needs_processing))
                    
                    for size, group in size_groups.items():
                        if self._interrupted:
                            break
                            
                        # If size count globally (this run + db) is 1, it's unique
                        total_count = len(group) + db_size_counts.get(size, 0)
                        
                        for entry in group:
                            if self._interrupted:
                                break
                                
                            db_file = existing_db_files.get(entry.rel_path)
                            quick_hash = self._compute_quick_hash(entry.filepath, entry.size)
                            # Preserve existing full_hash if file unmodified and not full scan
                            full_hash = db_file.get('full_hash') if (db_file and not full) else None
                            
                            if total_count <= 1:
                                unique_size_files += 1
                            else:
                                if quick_hash and full:
                                    full_hash = self._compute_full_hash(entry.filepath)

                            # Optimization #3: Batch DB operations
                            try:
                                self.db.upsert_file_raw(
                                    volume_id=volume_id,
                                    relative_path=entry.rel_path,
                                    file_size=entry.size,
                                    quick_hash=quick_hash,
                                    full_hash=full_hash,
                                    file_modified_at=entry.mtime,
                                    scanned_at=time.time(),
                                )
                                
                                if not db_file:
                                    new_files += 1
                                else:
                                    updated_files += 1
                                    
                                if (new_files + updated_files) % 200 == 0:
                                    self.db.commit()
                            except Exception as e:
                                self.db.rollback()
                                progress.console.print(f"[yellow]Error saving {entry.filepath}: {e}[/yellow]")
                                
                            progress.update(task, advance=1)
                    
                    self.db.commit()

            # --- Post-scan ---
            if not self._interrupted:
                try:
                    scan_prefix = str(scan_root_path.relative_to(vol_info.drive_letter)).replace('\\', '/')
                    if scan_prefix == ".":
                        scan_prefix = ""
                except ValueError:
                    scan_prefix = ""
                    
                self.db.mark_missing_files(volume_id, current_disk_files, prefix=scan_prefix)
                missing_set = set(existing_db_files.keys()) - current_disk_files
                deleted_files = len(missing_set)
                scan_status = 'completed'
            else:
                scan_status = 'interrupted'

            # Phase 4/4: Resolving full hashes (parallel)...
            if not self._interrupted:
                self._resolve_full_hashes_parallel(volume_id)

            self.db.refresh_duplicate_groups()

            self.db.complete_scan(
                scan_id, files_scanned, new_files, updated_files, scan_status
            )

            # Check for candidate duplicates on other (disconnected) volumes
            if not self._interrupted:
                pending = self.db.get_pending_cross_volume_hashes()
                pending_other = [f for f in pending if f['volume_id'] != volume_id]
                if pending_other:
                    by_vol = defaultdict(list)
                    for f in pending_other:
                        letter = (f.get('last_drive_letter') or "").rstrip("\\")
                        lbl = (f.get('volume_label') or "").strip()
                        if lbl and letter:
                            v_label = f"{lbl} ({letter})"
                        elif lbl:
                            v_label = lbl
                        elif letter:
                            v_label = f"Drive {letter}"
                        else:
                            v_label = f"Volume {f['volume_id']}"
                        by_vol[v_label].append(f)
                    
                    console = Console()
                    console.print("\n[yellow]Notice: Cross-volume duplicate candidates found![/yellow]")
                    for v_label, files_list in by_vol.items():
                        console.print(
                            f"  [yellow]• {v_label}[/yellow]: {len(files_list)} candidate file(s) waiting for full hash verification. "
                            f"(Insert this volume and run scan to complete verification)"
                        )

            duration = time.time() - start_time
            return ScanResult(
                volume_id=volume_id,
                scan_id=scan_id,
                files_scanned=files_scanned,
                new_files=new_files,
                updated_files=updated_files,
                skipped_files=skipped_files,
                deleted_files=deleted_files,
                unique_size_files=unique_size_files,
                duration=duration,
                interrupted=self._interrupted,
            )

        finally:
            signal.signal(signal.SIGINT, original_handler)

    def _compute_quick_hash(self, filepath: Path, file_size: int) -> str | None:
        """Hash first chunk + last chunk + file_size with mmap."""
        if file_size == 0:
            hasher = blake3.blake3()
            hasher.update(file_size.to_bytes(8, byteorder='little'))
            return hasher.hexdigest()
            
        chunk_size = self.config.quick_hash_chunk_size
        hasher = blake3.blake3()
        
        try:
            # Optimization #2: blake3 mmap
            with open(filepath, 'rb') as f:
                try:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        if file_size <= 2 * chunk_size:
                            hasher.update(mm)
                        else:
                            hasher.update(mm[:chunk_size])
                            hasher.update(mm[-chunk_size:])
                except (ValueError, OSError):
                    # Fallback to regular read
                    f.seek(0)
                    if file_size <= 2 * chunk_size:
                        hasher.update(f.read())
                    else:
                        hasher.update(f.read(chunk_size))
                        f.seek(-chunk_size, os.SEEK_END)
                        hasher.update(f.read(chunk_size))
        except (OSError, PermissionError):
            return None
            
        hasher.update(file_size.to_bytes(8, byteorder='little'))
        return hasher.hexdigest()

    def _compute_full_hash(self, filepath: Path, progress_callback=None) -> str | None:
        """Full blake3 hash with streaming chunks and real-time progress reporting."""
        try:
            file_size = filepath.stat().st_size
            if file_size == 0:
                if progress_callback:
                    progress_callback(0)
                return blake3.blake3().hexdigest()
                
            hasher = blake3.blake3()
            chunk_size = max(self.config.full_hash_chunk_size, 4 * 1024 * 1024)
            
            with open(filepath, 'rb') as f:
                while chunk := f.read(chunk_size):
                    if self._interrupted:
                        return None
                    hasher.update(chunk)
                    if progress_callback:
                        progress_callback(len(chunk))
                        
            return hasher.hexdigest()
        except (OSError, PermissionError):
            return None

    def _needs_rescan(self, db_file: dict, fs_modified: float) -> bool:
        if db_file.get('quick_hash') is None:
            return True
        return abs(db_file['file_modified_at'] - fs_modified) > 0.001

    def _enumerate_files(self, root: Path) -> Generator[tuple[Path, int, float], None, None]:
        """Optimization #5: stack-based os.scandir loop yielding (Path, size, mtime)"""
        min_size = self.config.min_file_size
        stack = [root]
        
        while stack:
            if self._interrupted:
                break
            current_dir = stack.pop()
            try:
                with os.scandir(current_dir) as it:
                    for entry in it:
                        if self._interrupted:
                            break
                        try:
                            if entry.is_symlink():
                                continue
                            try:
                                rel_path = str(Path(entry.path).relative_to(root)).replace('\\', '/')
                            except ValueError:
                                rel_path = ""

                            if entry.is_dir():
                                if not self._should_exclude_name(entry.name, rel_path=rel_path, is_dir=True):
                                    stack.append(Path(entry.path))
                            elif entry.is_file():
                                if not self._should_exclude_name(entry.name, rel_path=rel_path, is_dir=False):
                                    stat = entry.stat()
                                    if stat.st_size >= min_size:
                                        yield Path(entry.path), stat.st_size, stat.st_mtime
                        except (OSError, PermissionError):
                            continue
            except (OSError, PermissionError):
                continue

    def _resolve_full_hashes_parallel(self, volume_id: int):
        """Resolve full hashes for candidate duplicate files with live speed, byte progress and ETA."""
        # Files on current volume
        needs_hash = self.db.get_files_needing_full_hash(volume_id=volume_id)

        # Also check other files in DB needing full hash if their volume is currently connected and file exists
        all_needs = self.db.get_files_needing_full_hash()
        other_needs = [f for f in all_needs if f['volume_id'] != volume_id]
        for f in other_needs:
            vol = self.db.get_volume(f['volume_id'])
            if vol:
                drive_str = vol['last_drive_letter'] or ''
                if drive_str and not drive_str.endswith('\\'):
                    drive_str += '\\'
                test_path = Path(drive_str) / f['relative_path']
                if test_path.is_file():
                    needs_hash.append(f)

        if not needs_hash or self._interrupted:
            return

        total_files = len(needs_hash)
        total_bytes = sum(f['file_size'] for f in needs_hash)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold magenta]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            overall_task = progress.add_task(
                f"[magenta]Phase 4/4: Full hashes ({total_files} files)",
                total=max(total_bytes, 1)
            )
            file_task = progress.add_task(
                "[cyan]Preparing...",
                total=100
            )

            for idx, db_file in enumerate(needs_hash, 1):
                if self._interrupted:
                    break

                vol = self.db.get_volume(db_file['volume_id'])
                if not vol:
                    continue

                drive_str = vol['last_drive_letter'] or ''
                if drive_str and not drive_str.endswith('\\'):
                    drive_str += '\\'
                filepath = Path(drive_str) / db_file['relative_path']

                file_size = db_file['file_size']
                file_name = filepath.name

                if not filepath.is_file():
                    progress.console.print(f"[yellow]File not found: {filepath}[/yellow]")
                    progress.update(overall_task, advance=file_size)
                    continue

                progress.reset(
                    file_task,
                    total=max(file_size, 1),
                    description=f"[cyan]({idx}/{total_files}) {file_name}"
                )

                def on_chunk(bytes_read: int):
                    progress.update(file_task, advance=bytes_read)
                    progress.update(overall_task, advance=bytes_read)

                try:
                    full_hash = self._compute_full_hash(filepath, progress_callback=on_chunk)
                    if full_hash and not self._interrupted:
                        self.db.set_full_hash(db_file['id'], full_hash)
                        # Periodic commit to save progress
                        if idx % 10 == 0:
                            self.db.commit()
                except Exception as e:
                    progress.console.print(
                        f"[yellow]Could not hash {db_file['relative_path']}: {e}[/yellow]"
                    )
                finally:
                    progress.update(file_task, completed=max(file_size, 1))

            self.db.commit()
