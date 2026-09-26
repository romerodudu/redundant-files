import tempfile
import time
from pathlib import Path
import pytest
from redundant_files.database import Database
from redundant_files.config import Config
from redundant_files.duplicates import DuplicateDetector

@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        yield db
        db.close()

def test_volume_crud_and_reset(temp_db):
    vol_id = temp_db.upsert_volume(
        serial_number="SERIAL123",
        marker_uuid="UUID-123",
        label="TestVol",
        total_size=1000000,
        filesystem="NTFS",
        drive_letter="E:\\",
    )
    assert vol_id is not None
    vol = temp_db.get_volume(vol_id)
    assert vol["label"] == "TestVol"
    assert vol["serial_number"] == "SERIAL123"

    # Add a scan
    scan_id = temp_db.create_scan(vol_id, "E:\\")
    temp_db.complete_scan(scan_id, files_scanned=10, new_files=10, updated_files=0)

    # Add files
    temp_db.upsert_file_raw(
        volume_id=vol_id,
        relative_path="file1.bin",
        file_size=2048,
        quick_hash="qh1",
        full_hash="fh1",
        file_modified_at=time.time(),
        scanned_at=time.time(),
    )
    temp_db.commit()

    files = temp_db.get_files_by_volume(vol_id)
    assert len(files) == 1

    # Reset volume (DB only)
    f_cleared, s_cleared = temp_db.reset_volume(vol_id, remove_volume=False)
    assert f_cleared == 1
    assert s_cleared == 1
    assert len(temp_db.get_files_by_volume(vol_id)) == 0

    # Volume itself should still exist
    assert temp_db.get_volume(vol_id) is not None

    # Reset with remove_volume=True
    temp_db.reset_volume(vol_id, remove_volume=True)
    assert temp_db.get_volume(vol_id) is None


def test_ignored_groups(temp_db):
    vol_id = temp_db.upsert_volume("S1", "U1", "V1", 1000, "NTFS", "C:\\")
    
    # Insert duplicate files
    temp_db.upsert_file_raw(vol_id, "f1.bin", 5000, "qh_same", "fh_same", 1.0, 1.0)
    temp_db.upsert_file_raw(vol_id, "f2.bin", 5000, "qh_same", "fh_same", 1.0, 1.0)
    temp_db.commit()
    temp_db.refresh_duplicate_groups()

    detector = DuplicateDetector(temp_db)
    dups = detector.find_duplicates(exclude_ignored=True)
    assert len(dups) == 1
    assert dups[0].full_hash == "fh_same"

    # Ignore group
    temp_db.ignore_group("fh_same", file_count=2, file_size=5000, reason="Intentional backup")
    assert temp_db.is_group_ignored("fh_same") is True

    # Excluded from find_duplicates
    dups_ignored = detector.find_duplicates(exclude_ignored=True)
    assert len(dups_ignored) == 0

    # Included when exclude_ignored=False
    dups_all = detector.find_duplicates(exclude_ignored=False)
    assert len(dups_all) == 1

    # Unignore
    temp_db.unignore_group("fh_same")
    assert temp_db.is_group_ignored("fh_same") is False
    assert len(detector.find_duplicates(exclude_ignored=True)) == 1


def test_size_counts(temp_db):
    vol_id = temp_db.upsert_volume("S2", "U2", "V2", 1000, "NTFS", "C:\\")
    temp_db.upsert_file_raw(vol_id, "a.bin", 100, None, None, 1.0, 1.0)
    temp_db.upsert_file_raw(vol_id, "b.bin", 100, None, None, 1.0, 1.0)
    temp_db.upsert_file_raw(vol_id, "c.bin", 200, None, None, 1.0, 1.0)
    temp_db.commit()

    counts = temp_db.get_size_counts({100, 200, 300})
    assert counts.get(100) == 2
    assert counts.get(200) == 1
    assert counts.get(300) is None


def test_volume_display_and_provenance(temp_db):
    vol1_id = temp_db.upsert_volume("SER1", "U1", "Elements", 1000, "NTFS", "K:\\")
    vol2_id = temp_db.upsert_volume("SER2", "U2", "My Passport", 1000, "NTFS", "E:\\")

    temp_db.upsert_file_raw(vol1_id, "Movies/matrix.mkv", 5000, "qh_m", "fh_matrix", 1.0, 1.0)
    temp_db.upsert_file_raw(vol2_id, "Backup/matrix.mkv", 5000, "qh_m", "fh_matrix", 1.0, 1.0)
    temp_db.commit()
    temp_db.refresh_duplicate_groups()

    detector = DuplicateDetector(temp_db)
    dups = detector.find_duplicates()

    assert len(dups) == 1
    files = dups[0].files
    assert len(files) == 2

    f1 = next(f for f in files if f.volume_id == vol1_id)
    f2 = next(f for f in files if f.volume_id == vol2_id)

    # Verify volume_label and volume_serial are properly loaded from DB (not empty)
    assert f1.volume_label == "Elements"
    assert f1.volume_serial == "SER1"
    assert f1.volume_display_name == "Elements (K:)"
    assert f1.full_path == "K:\\Movies/matrix.mkv" or f1.full_path == "K:\\Movies\\matrix.mkv"

    assert f2.volume_label == "My Passport"
    assert f2.volume_serial == "SER2"
    assert f2.volume_display_name == "My Passport (E:)"
    assert f2.full_path == "E:\\Backup/matrix.mkv" or f2.full_path == "E:\\Backup\\matrix.mkv"

