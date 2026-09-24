import ctypes
from ctypes import wintypes
import string
from dataclasses import dataclass
from pathlib import Path
import uuid
import time
from typing import TYPE_CHECKING, Tuple, List, Optional

if TYPE_CHECKING:
    from .database import Database
    from rich.console import Console

@dataclass
class VolumeInfo:
    serial_number: str
    label: str
    drive_letter: str
    total_size: int
    free_size: int
    filesystem: str
    marker_uuid: Optional[str]

MARKER_FILENAME = ".redundant_files_volume_id"

def get_volume_info(drive_letter: str) -> VolumeInfo:
    """Get volume information using Win32 API (GetVolumeInformationW + GetDiskFreeSpaceExW).
    drive_letter should be like 'E' or 'E:' or 'E:\\'."""
    drive_letter = drive_letter.strip().upper()
    if len(drive_letter) == 1:
        root_path = f"{drive_letter}:\\"
    elif len(drive_letter) == 2 and drive_letter[1] == ':':
        root_path = f"{drive_letter}\\"
    elif len(drive_letter) == 3 and drive_letter[1:] == ':\\':
        root_path = drive_letter
    else:
        root_path = drive_letter
        if not root_path.endswith('\\'):
            root_path += '\\'

    # GetVolumeInformationW
    volume_name_buffer = ctypes.create_unicode_buffer(261)
    file_system_name_buffer = ctypes.create_unicode_buffer(261)
    serial_number = wintypes.DWORD()
    max_component_length = wintypes.DWORD()
    file_system_flags = wintypes.DWORD()

    success = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root_path),
        volume_name_buffer,
        ctypes.sizeof(volume_name_buffer),
        ctypes.byref(serial_number),
        ctypes.byref(max_component_length),
        ctypes.byref(file_system_flags),
        file_system_name_buffer,
        ctypes.sizeof(file_system_name_buffer)
    )

    if not success:
        raise OSError(f"Failed to get volume info for {root_path}")

    label = volume_name_buffer.value
    filesystem = file_system_name_buffer.value
    serial_hex = f"{serial_number.value:08X}"

    # GetDiskFreeSpaceExW
    free_bytes_available = wintypes.ULARGE_INTEGER()
    total_number_of_bytes = wintypes.ULARGE_INTEGER()
    total_number_of_free_bytes = wintypes.ULARGE_INTEGER()

    success_space = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(root_path),
        ctypes.byref(free_bytes_available),
        ctypes.byref(total_number_of_bytes),
        ctypes.byref(total_number_of_free_bytes)
    )

    if not success_space:
        raise OSError(f"Failed to get disk space for {root_path}")

    total_size = total_number_of_bytes.value
    free_size = free_bytes_available.value

    # Read marker
    marker_path = Path(root_path) / MARKER_FILENAME
    marker_uuid = None
    if marker_path.exists():
        try:
            marker_uuid = marker_path.read_text(encoding='utf-8').strip()
        except OSError:
            pass

    return VolumeInfo(
        serial_number=serial_hex,
        label=label,
        drive_letter=root_path,
        total_size=total_size,
        free_size=free_size,
        filesystem=filesystem,
        marker_uuid=marker_uuid if marker_uuid else None
    )

def ensure_marker(drive_letter: str) -> str:
    """Ensure a marker file exists on the volume. Returns the UUID from the marker."""
    drive_letter = drive_letter.strip().upper()
    if len(drive_letter) == 1:
        root_path = f"{drive_letter}:\\"
    elif len(drive_letter) == 2 and drive_letter[1] == ':':
        root_path = f"{drive_letter}\\"
    elif len(drive_letter) == 3 and drive_letter[1:] == ':\\':
        root_path = drive_letter
    else:
        root_path = drive_letter
        if not root_path.endswith('\\'):
            root_path += '\\'

    marker_path = Path(root_path) / MARKER_FILENAME
    if marker_path.exists():
        try:
            marker_uuid = marker_path.read_text(encoding='utf-8').strip()
            if marker_uuid:
                return marker_uuid
        except OSError:
            pass
    
    new_uuid = str(uuid.uuid4())
    try:
        marker_path.write_text(new_uuid, encoding='utf-8')
    except OSError as e:
        print(f"Warning: Could not write marker file to {marker_path}: {e}")
    return new_uuid

def identify_volume(db: "Database", drive_letter: str) -> Tuple[int, VolumeInfo]:
    """Identify a volume: check serial in DB, then marker file, else register new.
    Returns (volume_id, VolumeInfo)."""
    info = get_volume_info(drive_letter)
    
    vol = db.get_volume_by_serial(info.serial_number)
    if not vol and info.marker_uuid:
        vol = db.get_volume_by_marker(info.marker_uuid)
    
    marker_uuid = info.marker_uuid
    if not marker_uuid:
        marker_uuid = ensure_marker(drive_letter)
        info.marker_uuid = marker_uuid
    else:
        # Ensure it exists just in case
        marker_uuid = ensure_marker(drive_letter)

    volume_id = db.upsert_volume(
        serial_number=info.serial_number,
        marker_uuid=marker_uuid,
        label=info.label,
        total_size=info.total_size,
        filesystem=info.filesystem,
        drive_letter=info.drive_letter
    )

    return volume_id, info

def detect_removable_drives() -> List[str]:
    """Detect all removable drives using GetDriveTypeW. Returns list of drive letters."""
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            letter = chr(65 + i)
            root_path = f"{letter}:\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root_path))
            # DRIVE_REMOVABLE = 2, DRIVE_FIXED = 3
            if drive_type in (2, 3):
                drives.append(root_path)
    return drives

def wait_for_volume(db: "Database", volume_id: int, volume_label: str, 
                    console: "Console") -> str:
    """Prompt user to insert a specific volume. Polls removable drives.
    Returns the drive letter where the volume was found."""
    vol = db.get_volume(volume_id)
    if not vol:
        raise ValueError(f"Volume {volume_id} not found in database")
    
    target_serial = vol['serial_number']
    target_marker = vol['marker_uuid']

    console.print(f"Please insert volume '{volume_label}' (volume id: {volume_id})...")

    while True:
        drives = detect_removable_drives()
        for drive in drives:
            try:
                info = get_volume_info(drive)
                if info.serial_number == target_serial or info.marker_uuid == target_marker:
                    console.print(f"[green]Volume '{volume_label}' found at {drive}[/green]")
                    db.update_volume_drive_letter(volume_id, drive)
                    return drive
            except OSError:
                pass
        time.sleep(2.0)
