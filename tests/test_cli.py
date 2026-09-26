import tempfile
from pathlib import Path
from click.testing import CliRunner
from redundant_files.cli import cli
from redundant_files.database import Database

def test_cli_status_and_volumes():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        # Test status on empty db
        result = runner.invoke(cli, ["--db", str(db_path), "status"])
        assert result.exit_code == 0
        assert "Status" in result.output
        assert "Config file:" in result.output

        # Test volumes on empty db
        result = runner.invoke(cli, ["--db", str(db_path), "volumes"])
        assert result.exit_code == 0
        assert "Volumes" in result.output

        # Test ignored on empty db
        result = runner.invoke(cli, ["--db", str(db_path), "ignored"])
        assert result.exit_code == 0
        assert "No ignored groups" in result.output

def test_cli_reset_command():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        vol_id = db.upsert_volume("SN1", "U1", "Vol1", 1000, "NTFS", "C:\\")
        db.upsert_file_raw(vol_id, "f.txt", 100, "qh", "fh", 1.0, 1.0)
        db.commit()
        db.close()

        # Reset with confirmation 'y'
        result = runner.invoke(cli, ["--db", str(db_path), "reset", str(vol_id)], input="y\n")
        assert result.exit_code == 0
        assert "Reset complete" in result.output

        # Verify DB cleared
        db = Database(db_path)
        assert len(db.get_files_by_volume(vol_id)) == 0
        db.close()
