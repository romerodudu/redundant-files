# Redundant Files

A fast, memory-efficient command-line tool for detecting, comparing, and managing duplicate files across multiple volumes (including removable USB drives) in Windows.

[![Tests](https://img.shields.io/badge/tests-15%20passed-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![Platform](https://img.shields.io/badge/platform-windows-lightgrey.svg)]()

> 📖 **Documentación adicional:**
> - [📘 Manual de Usuario en Español (`MANUAL.md`)](MANUAL.md) — Guía completa paso a paso, ejemplos y solución de problemas.
> - [📋 Requisitos de Usuario (`REQUISITOS_USUARIO.md`)](REQUISITOS_USUARIO.md) — Especificación formal y trazabilidad de requerimientos.

---

## Key Features

- **High-Performance 4-Phase Pipeline**:
  1. *Stack-based `os.scandir`* enumeration with Windows metadata caching.
  2. *Incremental change detection* based on modification times (instant skipping of unchanged files).
  3. *Universal 128 KB Quick Hash* (first 64 KB + last 64 KB) enabling cross-volume matching without reading full files.
  4. *Multi-threaded parallel BLAKE3 hashing* with real-time speed (MB/s), byte counts, progress bars, and ETA.
- **Cross-Volume Duplicate Detection**: Matches duplicate files across different physical hard drives and external USB disks, even when drives are scanned independently or swapped.
- **Explicit Volume Provenance**: Displays the source volume label and drive letter clearly in all views: `[Elements (K:)] K:\Path\to\file.ext`.
- **Full-Screen Ncurses TUI (`redundant-files cleanup`)**:
  - Dual-panel interface (duplicate groups on left, files in group on right).
  - Clean two-line file layout with dedicated **Full Path Details Box** at the bottom.
  - **Horizontal scroll (`[← / →]` or `[h / l]`)** to inspect long filenames and deep folder structures.
  - Interactive status bar with live counts of files to delete, bytes saved, and ignored groups.
- **Pre-Deletion Confirmation Screen**: Clear side-by-side summary table showing all selected files to delete alongside their kept copies before final `[y/N]` confirmation.
- **Safe by Design**: Physically prevents accidental deletion of all copies in a group (at least one copy must be kept).
- **Two-Pass Safe Deletion**: Optionally move duplicates to a `DUPLICATED/` staging folder before final deletion, minimizing physical drive swaps.
- **Local & Global Config Discovery**: Automatically prioritizes `./config.yaml` in the current working directory before falling back to `~/.redundant-files/config.yaml`. Case-insensitive exclusion matching for directories, extensions, and glob patterns.
- **Volume Tracking**: Uniquely identifies volumes using Win32 API serial numbers and marker UUIDs, even if Windows reassigns drive letters.
- **Database Reset**: Reset scanned information for any volume from the database without touching real files on disk.

---

## Installation

### Option 1: Editable installation with development tools
```powershell
pip install -e ".[dev]"
```

### Option 2: Using requirements file
```powershell
pip install -r requirements.txt

# For running tests:
pip install -r requirements-dev.txt
```

---

## Commands & Usage

### 1. Scan Files
```powershell
# Scan an entire drive or folder
redundant-files scan E:
redundant-files scan E:\Movies

# Force a full re-hash of all files
redundant-files scan --full E:

# Resume an interrupted scan (progress is automatically saved on Ctrl+C)
redundant-files scan --resume E:
```

### 2. View Duplicates
```powershell
# Show all duplicate groups
redundant-files duplicates

# Filter by minimum file size (supports B, KB, MB, GB, TB)
redundant-files duplicates --min-size 100MB
```

### 3. Interactive Cleanup (Full-screen ncurses TUI)
```powershell
# Launch interactive dual-panel curses TUI
redundant-files cleanup

# Two-pass mode: Stage duplicates into DUPLICATED/ directory first
redundant-files cleanup --two-pass

# Pass 2: Permanently remove staged DUPLICATED/ folders with guided volume swaps
redundant-files cleanup --pass2

# Include previously ignored groups in cleanup
redundant-files cleanup --include-ignored
```

#### TUI Keyboard Shortcuts:
| Shortcut | Action |
| :--- | :--- |
| **`[↑ / ↓]`** or **`[j / k]`** | Navigate up / down in the active panel |
| **`[Tab]`** | Switch focus between Groups list (left) and Files list (right) |
| **`[← / →]`** or **`[h / l]`** | Horizontal scrolling to read long filenames and full paths |
| **`[Space]`** | Toggle delete / keep for highlighted file (`[ ] (KEEP)` vs `[*] (DEL)`) |
| **`[a]`** | Auto-select current group (keeps oldest file, marks copies for deletion) |
| **`[A]`** | Auto-select **all** duplicate groups at once |
| **`[i]`** | Mark active group as IGNORED (skipped now and in future scans) |
| **`[s]`** | Reset / unmark active group |
| **`[Enter]`** or **`[c]`** | Confirm plan and proceed to pre-deletion summary table |
| **`[q]`** or **`[Esc]`** | Cancel and exit without making changes |

### 4. Ignore Management
```powershell
# List all ignored duplicate groups
redundant-files ignored

# Interactive checkbox menu to un-ignore specific groups
redundant-files ignored --manage

# Clear all ignored groups
redundant-files ignored --clear
```

### 5. Volume Management & Database Reset
```powershell
# List all indexed volumes
redundant-files volumes

# Check overall database status (volumes, files, duplicates, pending cross-volume checks)
redundant-files status

# Clear database records for a volume (real files on disk are NEVER touched)
redundant-files reset <volume_id>

# Clear records and remove the volume entry from the database
redundant-files reset <volume_id> --remove-volume
```

---

## Configuration

The configuration file is discovered automatically in `./config.yaml` (current working directory) or `~/.redundant-files/config.yaml`:

```yaml
# Minimum file size to index (bytes)
# 524288000 = 500 MB (ideal for large videos, ISOs, VM disks)
min_file_size: 524288000

# Directories to exclude (case-insensitive)
exclude_dirs:
  - "$RECYCLE.BIN"
  - "System Volume Information"
  - ".git"
  - "node_modules"

# File extensions to exclude (case-insensitive)
exclude_extensions:
  - "tmp"
  - "log"
  - "bak"

# Path glob patterns to exclude (case-insensitive)
exclude_patterns:
  - "**/cache/*"
  - "**/temp/*"
```

---

## Running Tests

```powershell
pytest
```
All 15 automated unit tests cover scanner grouping, case-insensitive path exclusions, cross-volume matching, quick hash backfill, TUI plan construction, safety constraints, horizontal scrolling, and volume provenance formatting.
