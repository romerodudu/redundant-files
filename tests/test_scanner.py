import tempfile
import shutil
from pathlib import Path
import pytest
from redundant_files.database import Database
from redundant_files.config import Config
from redundant_files.scanner import Scanner

def test_scanner_size_pregrouping_and_exclusions():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()

        # Create config with low min_file_size for testing
        config = Config()
        config.min_file_size = 100
        config.exclude_extensions = ["tmp", "log"]
        config.exclude_dirs = [".git", "ignored_dir"]

        db = Database(db_path)
        scanner = Scanner(db, config)

        # Create test files
        # 1. Two duplicate files of 500 bytes
        content_dup = b"D" * 500
        (scan_dir / "dup1.bin").write_bytes(content_dup)
        (scan_dir / "dup2.bin").write_bytes(content_dup)

        # 2. Unique size file of 600 bytes
        (scan_dir / "unique.bin").write_bytes(b"U" * 600)

        # 3. Excluded file by extension
        (scan_dir / "temp.tmp").write_bytes(b"T" * 500)

        # 4. Excluded directory
        ignored_sub = scan_dir / "ignored_dir"
        ignored_sub.mkdir()
        (ignored_sub / "nested.bin").write_bytes(b"N" * 500)

        drive_letter = str(scan_dir.anchor)[:2]
        res = scanner.scan(drive_letter=drive_letter, root_path=str(scan_dir), full=False)

        assert res.interrupted is False
        assert res.files_scanned == 3  # dup1.bin, dup2.bin, unique.bin (others excluded)
        assert res.unique_size_files == 1  # unique.bin should not be hashed
        assert res.new_files == 3

        # Verify duplicate groups in DB
        groups = db.get_duplicate_groups()
        assert len(groups) == 1
        assert groups[0]["file_count"] == 2

        # Check unique file in DB has quick_hash (for cross-volume matching) but no full_hash
        files = db.get_files_by_volume(res.volume_id)
        unique_file = next(f for f in files if "unique.bin" in f["relative_path"])
        assert unique_file["quick_hash"] is not None
        assert unique_file["full_hash"] is None

        # Check duplicate files have hashes
        dup_file = next(f for f in files if "dup1.bin" in f["relative_path"])
        assert dup_file["quick_hash"] is not None
        assert dup_file["full_hash"] is not None

        db.close()

def test_scanner_case_insensitive_and_path_exclusions():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()

        config = Config()
        config.min_file_size = 100
        # Test case-insensitive directory, glob pattern, and extension
        config.exclude_dirs = ["SUBDIR"]
        config.exclude_patterns = ["*.BAK", "special/*"]

        db = Database(db_path)
        scanner = Scanner(db, config)

        # Files that should be included
        (scan_dir / "valid.txt").write_bytes(b"V" * 200)

        # File excluded by case-insensitive pattern (*.BAK excludes file.bak)
        (scan_dir / "file.bak").write_bytes(b"B" * 200)

        # File in case-insensitive directory (SUBDIR excludes subdir/nested.txt)
        sub = scan_dir / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_bytes(b"N" * 200)

        # File excluded by path pattern (special/*)
        spec = scan_dir / "special"
        spec.mkdir()
        (spec / "item.txt").write_bytes(b"S" * 200)

        drive_letter = str(scan_dir.anchor)[:2]
        res = scanner.scan(drive_letter=drive_letter, root_path=str(scan_dir))

        assert res.files_scanned == 1
        db.close()

def test_cross_volume_duplicate_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir)
        db = Database(p / "test.db")
        cfg = Config()
        cfg.min_file_size = 100

        # Simulate two different volumes
        vol1_id = db.upsert_volume("SERIAL_1", "UUID1", "USB_1", 1000000, "NTFS", str(p))
        vol2_id = db.upsert_volume("SERIAL_2", "UUID2", "USB_2", 1000000, "NTFS", str(p))

        # File on Vol1 (scanned first, unique at that time)
        f1 = p / "file_vol1.bin"
        f1.write_bytes(b"SHARED_CONTENT" * 20)
        db.upsert_file_raw(vol1_id, "file_vol1.bin", len(b"SHARED_CONTENT" * 20), "quick_shared", None, 1.0, 1.0)
        db.commit()

        # File on Vol2 (scanned later, exact duplicate)
        f2 = p / "file_vol2.bin"
        f2.write_bytes(b"SHARED_CONTENT" * 20)
        db.upsert_file_raw(vol2_id, "file_vol2.bin", len(b"SHARED_CONTENT" * 20), "quick_shared", None, 1.0, 1.0)
        db.commit()

        scanner = Scanner(db, cfg)
        scanner._resolve_full_hashes_parallel(vol2_id)
        db.refresh_duplicate_groups()

        from redundant_files.duplicates import DuplicateDetector
        detector = DuplicateDetector(db)
        dups = detector.find_duplicates()

        assert len(dups) == 1
        assert dups[0].file_count == 2
        vol_ids = {f.volume_id for f in dups[0].files}
        assert vol_ids == {vol1_id, vol2_id}

        db.close()

def test_rescan_backfills_missing_quick_hash_and_preserves_full_hash():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()

        config = Config()
        config.min_file_size = 50
        db = Database(db_path)
        scanner = Scanner(db, config)

        test_file = scan_dir / "legacy.bin"
        content = b"L" * 100
        test_file.write_bytes(content)

        drive_letter = str(scan_dir.anchor)[:2]
        res1 = scanner.scan(drive_letter=drive_letter, root_path=str(scan_dir))
        assert res1.files_scanned == 1

        files = db.get_files_by_volume(res1.volume_id)
        f = files[0]
        # Simulate legacy scan where quick_hash was NULL and full_hash had a value
        db.conn.execute("UPDATE files SET quick_hash = NULL, full_hash = 'PRESERVED_HASH' WHERE id = ?", (f["id"],))
        db.conn.commit()

        # Rescan without --full
        res2 = scanner.scan(drive_letter=drive_letter, root_path=str(scan_dir), full=False)
        assert res2.updated_files == 1  # Should have been processed to backfill quick_hash

        updated_f = db.get_files_by_volume(res1.volume_id)[0]
        assert updated_f["quick_hash"] is not None
        assert updated_f["full_hash"] == "PRESERVED_HASH"

        db.close()


