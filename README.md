# Redundant Files

A command-line tool for detecting and managing duplicate files across multiple volumes, including removable drives.

## Installation

```bash
pip install -e .
```

## Usage

List of commands:
- `redundant-files scan <path>`: Scan a directory or volume for files.
- `redundant-files volumes`: List all known volumes.
- `redundant-files duplicates`: Show duplicate file groups.
- `redundant-files cleanup`: Interactive duplicate cleanup.
- `redundant-files status`: Show database status and statistics.
