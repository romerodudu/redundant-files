from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database
    from .cleanup import CleanupPlan, CleanupAction

@dataclass
class InsertionStep:
    volume_id: int
    volume_label: str
    actions: list[CleanupAction]
    
    @property
    def space_freed(self) -> int:
        return sum(a.file_to_delete.file_size for a in self.actions)

class InsertionScheduler:
    def __init__(self, db: Database):
        self.db = db
    
    def plan_insertions(self, plan: CleanupPlan) -> list[InsertionStep]:
        """Create an ordered list of volume insertions that minimizes swaps."""
        actions_by_vol = plan.actions_by_volume()
        
        steps = []
        for vid, actions in actions_by_vol.items():
            vol_dict = self.db.get_volume(vid)
            vol_label = vol_dict["label"] if vol_dict else f"Volume {vid}"
            steps.append(InsertionStep(volume_id=vid, volume_label=vol_label, actions=actions))
            
        steps.sort(key=lambda s: len(s.actions), reverse=True)
        return steps
    
    def plan_pass2_insertions(self, pending_volumes: list[dict]) -> list[InsertionStep]:
        """Plan insertions for pass 2 cleanup."""
        steps = []
        for vol in pending_volumes:
            steps.append(InsertionStep(
                volume_id=vol["id"],
                volume_label=vol.get("label", f"Volume {vol['id']}"),
                actions=[]
            ))
        return steps
