from dataclasses import dataclass, field
from pathlib import Path
import yaml

DEFAULT_CONFIG_DIR = Path.home() / ".redundant-files"
DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "redundant_files.db"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"

@dataclass
class Config:
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    min_file_size: int = 1_048_576  # 1MB default
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "*.tmp", "*.temp", "Thumbs.db", "desktop.ini", ".DS_Store",
        "*.lnk", "~$*",
    ])
    exclude_extensions: list[str] = field(default_factory=lambda: [])
    exclude_dirs: list[str] = field(default_factory=lambda: [
        "$RECYCLE.BIN", "System Volume Information", ".Trash-*",
        "DUPLICATED",  # Our own folder
    ])
    quick_hash_chunk_size: int = 65_536  # 64KB
    full_hash_chunk_size: int = 1_048_576  # 1MB
    
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from YAML file. Create default if not exists."""
        if path is None:
            path = DEFAULT_CONFIG_PATH
            
        if not path.exists():
            return cls.ensure_defaults()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "db_path" in data and data["db_path"] is not None:
            data["db_path"] = Path(data["db_path"])
        else:
            data.pop("db_path", None)
            
        # Filter valid keys
        valid_keys = cls.__dataclass_fields__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
            
        return cls(**filtered_data)
    
    def save(self, path: Path | None = None) -> None:
        """Save config to YAML file."""
        if path is None:
            path = DEFAULT_CONFIG_PATH
            
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "db_path": str(self.db_path) if self.db_path else None,
            "min_file_size": self.min_file_size,
            "exclude_patterns": self.exclude_patterns,
            "exclude_extensions": self.exclude_extensions,
            "exclude_dirs": self.exclude_dirs,
            "quick_hash_chunk_size": self.quick_hash_chunk_size,
            "full_hash_chunk_size": self.full_hash_chunk_size,
        }
        
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, sort_keys=False)
    
    @classmethod
    def default_config_path(cls) -> Path:
        return DEFAULT_CONFIG_PATH
    
    @classmethod
    def ensure_defaults(cls) -> "Config":
        """Load config, creating default file if it doesn't exist."""
        path = DEFAULT_CONFIG_PATH
        if path.exists():
            return cls.load(path)
        
        config = cls()
        config.save(path)
        return config
