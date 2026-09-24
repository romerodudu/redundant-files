import sqlite3
from pathlib import Path
import time

class Database:
    def __init__(self, db_path: Path):
        """Open/create database and initialize schema."""
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
    
    def _init_schema(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS volumes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial_number TEXT UNIQUE,
                    marker_uuid TEXT UNIQUE,
                    label TEXT,
                    total_size INTEGER,
                    filesystem TEXT,
                    last_drive_letter TEXT,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    volume_id INTEGER NOT NULL REFERENCES volumes(id),
                    scan_root TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL,
                    files_scanned INTEGER DEFAULT 0,
                    new_files INTEGER DEFAULT 0,
                    updated_files INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    volume_id INTEGER NOT NULL REFERENCES volumes(id),
                    relative_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    quick_hash TEXT,
                    full_hash TEXT,
                    file_modified_at REAL,
                    scanned_at REAL,
                    is_deleted INTEGER DEFAULT 0,
                    UNIQUE(volume_id, relative_path)
                );

                CREATE INDEX IF NOT EXISTS idx_files_size ON files(file_size) WHERE is_deleted = 0;
                CREATE INDEX IF NOT EXISTS idx_files_quick_hash ON files(quick_hash) WHERE is_deleted = 0;
                CREATE INDEX IF NOT EXISTS idx_files_full_hash ON files(full_hash) WHERE is_deleted = 0 AND full_hash IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_files_volume ON files(volume_id);

                CREATE TABLE IF NOT EXISTS duplicate_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_hash TEXT UNIQUE NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_count INTEGER NOT NULL
                );
            """)
            # Migration: add status column to scans if missing
            try:
                self.conn.execute("SELECT status FROM scans LIMIT 1")
            except sqlite3.OperationalError:
                self.conn.execute("ALTER TABLE scans ADD COLUMN status TEXT DEFAULT 'completed'")
    
    # --- Volumes ---
    def upsert_volume(self, serial_number: str, marker_uuid: str, label: str,
                      total_size: int, filesystem: str, drive_letter: str) -> int:
        """Insert or update a volume. Returns volume_id."""
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO volumes (serial_number, marker_uuid, label, total_size, filesystem, last_drive_letter, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(serial_number) DO UPDATE SET
                    marker_uuid=excluded.marker_uuid,
                    label=excluded.label,
                    total_size=excluded.total_size,
                    filesystem=excluded.filesystem,
                    last_drive_letter=excluded.last_drive_letter,
                    last_seen=excluded.last_seen
                RETURNING id
                """,
                (serial_number, marker_uuid, label, total_size, filesystem, drive_letter, now, now)
            )
            return cur.fetchone()[0]
    
    def get_volume(self, volume_id: int) -> dict | None:
        cur = self.conn.execute("SELECT * FROM volumes WHERE id = ?", (volume_id,))
        row = cur.fetchone()
        return dict(row) if row else None
        
    def get_volume_by_serial(self, serial_number: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM volumes WHERE serial_number = ?", (serial_number,))
        row = cur.fetchone()
        return dict(row) if row else None
        
    def get_volume_by_marker(self, marker_uuid: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM volumes WHERE marker_uuid = ?", (marker_uuid,))
        row = cur.fetchone()
        return dict(row) if row else None
        
    def list_volumes(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM volumes ORDER BY last_seen DESC")
        return [dict(row) for row in cur.fetchall()]
        
    def update_volume_drive_letter(self, volume_id: int, drive_letter: str):
        now = time.time()
        with self.conn:
            self.conn.execute("UPDATE volumes SET last_drive_letter = ?, last_seen = ? WHERE id = ?", (drive_letter, now, volume_id))
    
    # --- Scans ---
    def create_scan(self, volume_id: int, scan_root: str) -> int:
        """Create a new scan record. Returns scan_id."""
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO scans (volume_id, scan_root, started_at, status) VALUES (?, ?, ?, 'running')",
                (volume_id, scan_root, now)
            )
            return cur.lastrowid
    
    def complete_scan(self, scan_id: int, files_scanned: int,
                      new_files: int, updated_files: int,
                      status: str = 'completed'):
        now = time.time()
        with self.conn:
            self.conn.execute(
                """
                UPDATE scans 
                SET completed_at = ?, files_scanned = ?, new_files = ?, updated_files = ?, status = ?
                WHERE id = ?
                """,
                (now, files_scanned, new_files, updated_files, status, scan_id)
            )
    
    def get_last_scan(self, volume_id: int) -> dict | None:
        """Get the most recent scan for a volume."""
        cur = self.conn.execute(
            "SELECT * FROM scans WHERE volume_id = ? ORDER BY started_at DESC LIMIT 1",
            (volume_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    
    # --- Files ---
    def upsert_file(self, volume_id: int, relative_path: str, file_size: int,
                    quick_hash: str, full_hash: str | None,
                    file_modified_at: float, scanned_at: float) -> int:
        """Insert or update a file record. Returns file_id."""
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO files (volume_id, relative_path, file_size, quick_hash, full_hash, file_modified_at, scanned_at, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(volume_id, relative_path) DO UPDATE SET
                    file_size=excluded.file_size,
                    quick_hash=excluded.quick_hash,
                    full_hash=excluded.full_hash,
                    file_modified_at=excluded.file_modified_at,
                    scanned_at=excluded.scanned_at,
                    is_deleted=0
                RETURNING id
                """,
                (volume_id, relative_path, file_size, quick_hash, full_hash, file_modified_at, scanned_at)
            )
            return cur.fetchone()[0]
    
    def get_file(self, volume_id: int, relative_path: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM files WHERE volume_id = ? AND relative_path = ?", (volume_id, relative_path))
        row = cur.fetchone()
        return dict(row) if row else None
        
    def get_files_by_volume(self, volume_id: int, include_deleted: bool = False) -> list[dict]:
        if include_deleted:
            cur = self.conn.execute("SELECT * FROM files WHERE volume_id = ?", (volume_id,))
        else:
            cur = self.conn.execute("SELECT * FROM files WHERE volume_id = ? AND is_deleted = 0", (volume_id,))
        return [dict(row) for row in cur.fetchall()]
        
    def mark_file_deleted(self, file_id: int):
        with self.conn:
            self.conn.execute("UPDATE files SET is_deleted = 1 WHERE id = ?", (file_id,))
            
    def mark_missing_files(self, volume_id: int, existing_paths: set[str]):
        """Mark as deleted all non-deleted files for a volume whose relative_path is NOT in the provided set."""
        with self.conn:
            self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS current_paths (path TEXT)")
            self.conn.execute("DELETE FROM current_paths")
            self.conn.executemany("INSERT INTO current_paths (path) VALUES (?)", [(p,) for p in existing_paths])
            
            self.conn.execute(
                """
                UPDATE files
                SET is_deleted = 1
                WHERE volume_id = ? AND is_deleted = 0 AND relative_path NOT IN (SELECT path FROM current_paths)
                """,
                (volume_id,)
            )
            self.conn.execute("DROP TABLE current_paths")
            
    def get_files_by_size(self, file_size: int, exclude_deleted: bool = True) -> list[dict]:
        if exclude_deleted:
            cur = self.conn.execute("SELECT * FROM files WHERE file_size = ? AND is_deleted = 0", (file_size,))
        else:
            cur = self.conn.execute("SELECT * FROM files WHERE file_size = ?", (file_size,))
        return [dict(row) for row in cur.fetchall()]
        
    def get_files_by_quick_hash(self, quick_hash: str, exclude_deleted: bool = True) -> list[dict]:
        if exclude_deleted:
            cur = self.conn.execute("SELECT * FROM files WHERE quick_hash = ? AND is_deleted = 0", (quick_hash,))
        else:
            cur = self.conn.execute("SELECT * FROM files WHERE quick_hash = ?", (quick_hash,))
        return [dict(row) for row in cur.fetchall()]
        
    def get_files_needing_full_hash(self) -> list[dict]:
        """Get files whose quick_hash appears more than once but full_hash is NULL."""
        cur = self.conn.execute(
            """
            SELECT f.*
            FROM files f
            JOIN (
                SELECT quick_hash
                FROM files
                WHERE is_deleted = 0
                GROUP BY quick_hash
                HAVING COUNT(*) > 1
            ) dup_quick ON f.quick_hash = dup_quick.quick_hash
            WHERE f.is_deleted = 0 AND f.full_hash IS NULL
            """
        )
        return [dict(row) for row in cur.fetchall()]
    
    def set_full_hash(self, file_id: int, full_hash: str):
        with self.conn:
            self.conn.execute("UPDATE files SET full_hash = ? WHERE id = ?", (full_hash, file_id))
    
    # --- Batch / Transaction helpers (optimization #3) ---
    def upsert_file_raw(self, volume_id: int, relative_path: str, file_size: int,
                        quick_hash: str | None, full_hash: str | None,
                        file_modified_at: float, scanned_at: float) -> int:
        """Insert or update a file record WITHOUT auto-committing.
        Caller is responsible for calling commit() periodically."""
        cur = self.conn.execute(
            """
            INSERT INTO files (volume_id, relative_path, file_size, quick_hash, full_hash, file_modified_at, scanned_at, is_deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(volume_id, relative_path) DO UPDATE SET
                file_size=excluded.file_size,
                quick_hash=excluded.quick_hash,
                full_hash=excluded.full_hash,
                file_modified_at=excluded.file_modified_at,
                scanned_at=excluded.scanned_at,
                is_deleted=0
            RETURNING id
            """,
            (volume_id, relative_path, file_size, quick_hash, full_hash, file_modified_at, scanned_at)
        )
        return cur.fetchone()[0]

    def get_size_counts(self, sizes: set[int]) -> dict[int, int]:
        """For a set of file sizes, return count of non-deleted files per size.
        Uses a temp table for efficient batch lookup (optimization #7)."""
        if not sizes:
            return {}
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _lookup_sizes (size INTEGER)")
        self.conn.execute("DELETE FROM _lookup_sizes")
        self.conn.executemany(
            "INSERT INTO _lookup_sizes VALUES (?)", [(s,) for s in sizes]
        )
        cur = self.conn.execute("""
            SELECT f.file_size, COUNT(*) as cnt
            FROM files f
            JOIN _lookup_sizes ls ON f.file_size = ls.size
            WHERE f.is_deleted = 0
            GROUP BY f.file_size
        """)
        result = {row['file_size']: row['cnt'] for row in cur.fetchall()}
        self.conn.execute("DROP TABLE IF EXISTS _lookup_sizes")
        self.conn.commit()
        return result

    def commit(self):
        """Commit the current transaction."""
        self.conn.commit()

    def rollback(self):
        """Rollback the current transaction."""
        self.conn.rollback()

    # --- Duplicate Groups ---
    def refresh_duplicate_groups(self):
        """Recalculate duplicate_groups from files table. 
        A group exists when 2+ non-deleted files share the same full_hash."""
        with self.conn:
            self.conn.execute("DELETE FROM duplicate_groups")
            self.conn.execute(
                """
                INSERT INTO duplicate_groups (full_hash, file_size, file_count)
                SELECT full_hash, file_size, COUNT(*) as c
                FROM files
                WHERE is_deleted = 0 AND full_hash IS NOT NULL
                GROUP BY full_hash
                HAVING COUNT(*) > 1
                """
            )
    
    def get_duplicate_groups(self, min_size: int = 0) -> list[dict]:
        """Returns list of {full_hash, file_size, file_count}."""
        cur = self.conn.execute("SELECT full_hash, file_size, file_count FROM duplicate_groups WHERE file_size >= ?", (min_size,))
        return [dict(row) for row in cur.fetchall()]
    
    def get_group_files(self, full_hash: str) -> list[dict]:
        """Returns files in a duplicate group, joined with volume info."""
        cur = self.conn.execute(
            """
            SELECT f.*, v.label as volume_label, v.serial_number as volume_serial, v.last_drive_letter
            FROM files f
            JOIN volumes v ON f.volume_id = v.id
            WHERE f.full_hash = ? AND f.is_deleted = 0
            """, (full_hash,)
        )
        return [dict(row) for row in cur.fetchall()]
    
    def close(self):
        self.conn.close()
