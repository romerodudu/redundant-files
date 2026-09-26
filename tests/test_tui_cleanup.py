from redundant_files.duplicates import DuplicateGroup, FileInfo
from redundant_files.tui_cleanup import CleanupTUI, format_size

def create_sample_groups():
    f1 = FileInfo(file_id=1, volume_id=1, volume_label="VOL1", volume_serial="S1",
                  last_drive_letter="D:\\", relative_path="Movies/old_movie.mkv",
                  file_size=1000, full_hash="hash_a", file_modified_at=100.0)
    f2 = FileInfo(file_id=2, volume_id=2, volume_label="VOL2", volume_serial="S2",
                  last_drive_letter="E:\\", relative_path="Backup/new_movie.mkv",
                  file_size=1000, full_hash="hash_a", file_modified_at=200.0)
    f3 = FileInfo(file_id=3, volume_id=1, volume_label="VOL1", volume_serial="S1",
                  last_drive_letter="D:\\", relative_path="Temp/movie_copy.mkv",
                  file_size=1000, full_hash="hash_a", file_modified_at=300.0)
    g1 = DuplicateGroup(full_hash="hash_a", file_size=1000, files=[f1, f2, f3])

    f4 = FileInfo(file_id=4, volume_id=1, volume_label="VOL1", volume_serial="S1",
                  last_drive_letter="D:\\", relative_path="Docs/doc1.pdf",
                  file_size=500, full_hash="hash_b", file_modified_at=50.0)
    f5 = FileInfo(file_id=5, volume_id=2, volume_label="VOL2", volume_serial="S2",
                  last_drive_letter="E:\\", relative_path="Docs/doc1_copy.pdf",
                  file_size=500, full_hash="hash_b", file_modified_at=60.0)
    g2 = DuplicateGroup(full_hash="hash_b", file_size=500, files=[f4, f5])

    return [g1, g2]

def test_tui_auto_select_and_build_plan():
    groups = create_sample_groups()
    tui = CleanupTUI(groups)

    # Initial state
    del_count, space_saved, ignored_count = tui.get_summary_stats()
    assert del_count == 0
    assert space_saved == 0
    assert ignored_count == 0

    # Auto select for group 0: should keep f1 (oldest, mod=100), delete f2 and f3
    tui.auto_select_group(groups[0])
    del_count, space_saved, ignored_count = tui.get_summary_stats()
    assert del_count == 2
    assert space_saved == 2000

    plan, ignored = tui.build_plan()
    assert len(plan.actions) == 2
    assert len(ignored) == 0
    assert plan.total_space_saved == 2000
    assert {a.file_to_delete.file_id for a in plan.actions} == {2, 3}
    assert all(a.file_to_keep.file_id == 1 for a in plan.actions)

def test_tui_safety_cannot_delete_all_copies():
    groups = create_sample_groups()
    tui = CleanupTUI(groups)
    tui.group_idx = 1  # Group 2 has 2 files (f4, f5)

    # Toggle file 0 (f4) -> marked for delete
    tui.file_idx = 0
    tui.toggle_current_file()
    assert 4 in tui.group_states["hash_b"]["deleted_ids"]

    # Try to toggle file 1 (f5) -> should be REJECTED because it's the last copy!
    tui.file_idx = 1
    tui.toggle_current_file()
    assert 5 not in tui.group_states["hash_b"]["deleted_ids"]
    assert "Cannot delete ALL copies" in tui.status_message

    # Un-toggle file 0
    tui.file_idx = 0
    tui.toggle_current_file()
    assert 4 not in tui.group_states["hash_b"]["deleted_ids"]

def test_tui_auto_select_all_and_ignore():
    groups = create_sample_groups()
    tui = CleanupTUI(groups)

    # Ignore group 0
    tui.group_idx = 0
    tui.toggle_ignore_current()
    assert tui.group_states["hash_a"]["is_ignored"] is True

    # Auto select all -> group 0 is ignored so only group 1 is auto-selected
    tui.auto_select_all()
    del_count, space_saved, ignored_count = tui.get_summary_stats()
    assert ignored_count == 1
    assert del_count == 1  # only f5 from group 1
    assert space_saved == 500

    plan, ignored = tui.build_plan()
    assert len(plan.actions) == 1
    assert plan.actions[0].file_to_delete.file_id == 5
    assert ignored == ["hash_a"]

def test_format_size():
    assert format_size(500) == "500.0 B"
    assert "MB" in format_size(10 * 1024 * 1024)
    assert "GB" in format_size(2 * 1024 * 1024 * 1024)

def test_confirm_plan_displays_table(monkeypatch):
    from rich.console import Console
    from redundant_files.ui import InteractiveUI
    from redundant_files.config import Config
    from io import StringIO

    groups = create_sample_groups()
    tui = CleanupTUI(groups)
    tui.auto_select_group(groups[0])
    plan, _ = tui.build_plan()

    out = StringIO()
    console = Console(file=out, color_system=None, width=120)
    ui = InteractiveUI(None, Config(), console)

    # Mock Confirm.ask to return True
    from rich.prompt import Confirm
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: True)

    confirmed = ui.confirm_plan(plan)
    assert confirmed is True
    output = out.getvalue()
    assert "Files Selected for Deletion" in output
    assert "Backup/new_movie.mkv" in output
    assert "Temp/movie_copy.mkv" in output
    assert "Kept Copy" in output
    assert "Cleanup Plan Summary" in output

