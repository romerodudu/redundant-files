import os
import signal
from dataclasses import dataclass
from pathlib import Path
import blake3
import time
import fnmatch
from typing import Generator, TYPE_CHECKING, Optional

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console

if TYPE_CHECKING:
    from .database import Database
    from .config import Config


@dataclass
class ScanResult:
    volume_id: int
    scan_id: int
    files_scanned: int
    new_files: int
    updated_files: int
    skipped_files: int
    deleted_files: int
    duration: float
    interrupted: bool


class Scanner:
    def __init__(self, db: "Database", config: "Config"):
        self.db = db
        self.config = config
        self._interrupted = False

    def _handle_interrupt(self, signum, frame):
        """Handle Ctrl+C gracefully by setting interrupted flag."""
        self._interrupted = True

    def scan(self, drive_letter: str, root_path: Optional[str] = None,
             full: bool = False, resume: bool = False) -> ScanResult:
        """Scan a volume/directory.

        Args:
            drive_letter: Drive letter to scan (e.g. 'E', 'E:', 'E:\\').
            root_path: Optional subdirectory to scan within the volume.
            full: If True, rehash every file regardless of modification time.
            resume: If True, explicitly resume from the last interrupted scan.
                    Without this flag, incremental scanning still skips unchanged
                    files, but --resume prints additional context.

        The scan can be interrupted at any time with Ctrl+C. Progress is saved
        automatically and a subsequent scan will skip already-processed files.
        """
        from .volume import identify_volume

        self._interrupted = False
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_interrupt)
        console = Console()

        try:
            start_time = time.time()

            # 1. Identify volume
            volume_id, vol_info = identify_volume(self.db, drive_letter)

            # 2. Check previous scan status
            scan_root = root_path if root_path else vol_info.drive_letter
            last_scan = self.db.get_last_scan(volume_id)
            if last_scan and last_scan.get('status') in ('interrupted', 'running'):
                console.print(
                    f"[yellow]Previous scan was interrupted "
                    f"({last_scan.get('files_scanned', '?')} files processed). "
                    f"Resuming — unchanged files will be skipped automatically.[/yellow]"
                )

            # 3. Create new scan record
            scan_id = self.db.create_scan(volume_id, scan_root)

            files_scanned = 0
            new_files = 0
            updated_files = 0
            skipped_files = 0

            existing_db_files = {
                f['relative_path']: f
                for f in self.db.get_files_by_volume(volume_id)
            }
            current_disk_files: set[str] = set()
            scan_root_path = Path(scan_root)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
                transient=True,
            ) as progress:
                task = progress.add_task("[cyan]Scanning files...", total=None)

                for filepath in self._enumerate_files(scan_root_path):
                    if self._interrupted:
                        progress.console.print(
                            "\n[yellow bold]⚠ Scan interrupted by user. "
                            "Progress has been saved.[/yellow bold]\n"
                            "[dim]Run the scan command again to resume "
                            "where you left off.[/dim]"
                        )
                        break

                    files_scanned += 1
                    try:
                        # Compute relative path from volume root
                        try:
                            rel_path = str(filepath.relative_to(vol_info.drive_letter))
                        except ValueError:
                            rel_path = str(filepath.relative_to(scan_root_path))

                        current_disk_files.add(rel_path)

                        stat = filepath.stat()
                        file_size = stat.st_size
                        fs_modified = stat.st_mtime

                        db_file = existing_db_files.get(rel_path)

                        # Skip unchanged files (automatic resume)
                        if not full and db_file and not self._needs_rescan(db_file, fs_modified):
                            skipped_files += 1
                            progress.update(
                                task, advance=1,
                                description=(
                                    f"[cyan]Scanned {files_scanned} "
                                    f"[dim](skipped {skipped_files})[/dim]"
                                ),
                            )
                            continue

                        # Hash the file
                        quick_hash = self._compute_quick_hash(filepath)
                        full_hash = None
                        if full:
                            full_hash = self._compute_full_hash(filepath)

                        self.db.upsert_file(
                            volume_id=volume_id,
                            relative_path=rel_path,
                            file_size=file_size,
                            quick_hash=quick_hash,
                            full_hash=full_hash,
                            file_modified_at=fs_modified,
                            scanned_at=time.time(),
                        )

                        if not db_file:
                            new_files += 1
                        else:
                            updated_files += 1

                        # Periodic commit to persist progress
                        if (new_files + updated_files) % 50 == 0:
                            self.db.conn.commit()

                        progress.update(
                            task, advance=1,
                            description=(
                                f"[cyan]Scanned {files_scanned} "
                                f"[dim](skipped {skipped_files})[/dim]"
                            ),
                        )

                    except (OSError, PermissionError) as e:
                        progress.console.print(
                            f"[yellow]Skipped {filepath}: {e}[/yellow]"
                        )
                        continue

            # --- Post-scan ---
            deleted_files = 0

            if not self._interrupted:
                # Only mark missing files when we've seen the entire tree
                self.db.mark_missing_files(volume_id, current_disk_files)
                missing_set = set(existing_db_files.keys()) - current_disk_files
                deleted_files = len(missing_set)
                scan_status = 'completed'
            else:
                scan_status = 'interrupted'

            # Resolve full hashes for potential duplicates (unless interrupted)
            if not self._interrupted:
                self._resolve_full_hashes(volume_id)

            # Always refresh duplicate groups with whatever data we have
            self.db.refresh_duplicate_groups()

            # Finalize scan record
            self.db.complete_scan(
                scan_id, files_scanned, new_files, updated_files, scan_status
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
                duration=duration,
                interrupted=self._interrupted,
            )

        finally:
            # Always restore the original signal handler
            signal.signal(signal.SIGINT, original_handler)

    # ------------------------------------------------------------------ #
    #  Hashing helpers                                                     #
    # ------------------------------------------------------------------ #

    def _compute_quick_hash(self, filepath: Path) -> str:
        """Hash first chunk + last chunk + file_size."""
        chunk_size = self.config.quick_hash_chunk_size
        file_size = filepath.stat().st_size
        hasher = blake3.blake3()

        with open(filepath, 'rb') as f:
            if file_size <= 2 * chunk_size:
                hasher.update(f.read())
            else:
                hasher.update(f.read(chunk_size))
                f.seek(-chunk_size, os.SEEK_END)
                hasher.update(f.read(chunk_size))

        hasher.update(file_size.to_bytes(8, byteorder='little'))
        return hasher.hexdigest()

    def _compute_full_hash(self, filepath: Path) -> str:
        """Full blake3 hash with streaming."""
        chunk_size = self.config.full_hash_chunk_size
        hasher = blake3.blake3()
        with open(filepath, 'rb') as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    # ------------------------------------------------------------------ #
    #  Scan helpers                                                        #
    # ------------------------------------------------------------------ #

    def _needs_rescan(self, db_file: dict, fs_modified: float) -> bool:
        """Check if file needs rehashing based on modification time."""
        return abs(db_file['file_modified_at'] - fs_modified) > 0.001

    def _should_exclude(self, path: Path, root: Path) -> bool:
        """Check if path should be excluded based on config."""
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            rel_parts = path.parts

        for part in rel_parts:
            if part in self.config.exclude_dirs:
                return True

        if path.suffix:
            ext = path.suffix.lstrip('.')
            if ext in [e.lstrip('.') for e in self.config.exclude_extensions]:
                return True

        for pattern in self.config.exclude_patterns:
            if fnmatch.fnmatch(path.name, pattern):
                return True

        return False

    def _enumerate_files(self, root: Path) -> Generator[Path, None, None]:
        """Walk directory tree yielding files that pass filters."""
        min_size = self.config.min_file_size

        for dirpath, dirnames, filenames in os.walk(root):
            # Modify dirnames in-place to skip excluded dirs
            dirnames[:] = [
                d for d in dirnames
                if not self._should_exclude(Path(dirpath) / d, root)
            ]

            for filename in filenames:
                filepath = Path(dirpath) / filename
                if self._should_exclude(filepath, root):
                    continue

                try:
                    if filepath.is_symlink():
                        continue
                    if filepath.stat().st_size >= min_size:
                        yield filepath
                except (OSError, PermissionError):
                    continue

    def _resolve_full_hashes(self, volume_id: int):
        """For files on this volume whose quick_hash matches other files,
        compute full_hash if not already done."""
        needs_hash = self.db.get_files_needing_full_hash()
        needs_hash = [f for f in needs_hash if f['volume_id'] == volume_id]

        if not needs_hash:
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            transient=True,
        ) as progress:
            task = progress.add_task(
                "[magenta]Computing full hashes...", total=len(needs_hash)
            )

            for db_file in needs_hash:
                if self._interrupted:
                    break

                try:
                    vol = self.db.get_volume(db_file['volume_id'])
                    if not vol:
                        continue
                    drive_letter = vol['last_drive_letter']
                    filepath = Path(drive_letter) / db_file['relative_path']

                    full_hash = self._compute_full_hash(filepath)
                    self.db.set_full_hash(db_file['id'], full_hash)
                except (OSError, PermissionError) as e:
                    progress.console.print(
                        f"[yellow]Could not hash "
                        f"{db_file['relative_path']}: {e}[/yellow]"
                    )
                finally:
                    progress.update(task, advance=1)
