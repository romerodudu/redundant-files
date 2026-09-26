from __future__ import annotations
import curses
from datetime import datetime
from typing import TYPE_CHECKING
from .cleanup import CleanupPlan, CleanupAction, FileToDelete, FileToKeep

if TYPE_CHECKING:
    from .duplicates import DuplicateGroup, FileInfo

def format_size(size_bytes: int) -> str:
    """Format bytes into human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:3.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:3.1f} PB"

def middle_truncate(text: str, max_len: int) -> str:
    """Truncate text in the middle if it exceeds max_len, preserving the start and filename at the end."""
    if len(text) <= max_len:
        return text
    if max_len <= 9:
        return text[:max_len]
    part_len = (max_len - 5) // 2
    return f"{text[:part_len]} ... {text[-(max_len - part_len - 5):]}"

class CleanupTUI:
    def __init__(self, groups: list[DuplicateGroup]):
        self.groups = groups
        
        # State per group: group_hash -> {'deleted_ids': set(), 'is_ignored': bool}
        self.group_states: dict[str, dict] = {}
        for g in self.groups:
            self.group_states[g.full_hash] = {
                'deleted_ids': set(),
                'is_ignored': False
            }
            
        self.group_idx = 0
        self.group_scroll = 0
        
        self.file_idx = 0
        self.file_scroll = 0
        self.file_hscroll = 0
        
        # 0 = left panel (groups), 1 = right panel (files)
        self.active_panel = 0
        
        self.status_message = "Ready. Use [Space] to toggle, [a] for auto, [Tab] to switch panels, [Enter] to confirm."
        self.confirmed = False

    def auto_select_group(self, group: DuplicateGroup):
        """Keep oldest file, mark all others for deletion."""
        state = self.group_states[group.full_hash]
        state['is_ignored'] = False
        oldest = min(group.files, key=lambda f: f.file_modified_at)
        state['deleted_ids'] = {f.file_id for f in group.files if f.file_id != oldest.file_id}
        self.status_message = f"Auto-selected group {self.group_idx + 1}: kept oldest copy."

    def auto_select_all(self):
        """Auto select for all groups that are not ignored."""
        count = 0
        for g in self.groups:
            state = self.group_states[g.full_hash]
            if not state['is_ignored']:
                oldest = min(g.files, key=lambda f: f.file_modified_at)
                state['deleted_ids'] = {f.file_id for f in g.files if f.file_id != oldest.file_id}
                count += 1
        self.status_message = f"Auto-selected {count} groups (kept oldest file in each)."

    def toggle_ignore_current(self):
        """Toggle ignore for current group."""
        if not self.groups:
            return
        g = self.groups[self.group_idx]
        state = self.group_states[g.full_hash]
        state['is_ignored'] = not state['is_ignored']
        if state['is_ignored']:
            state['deleted_ids'].clear()
            self.status_message = f"Group {self.group_idx + 1} marked as IGNORED."
        else:
            self.status_message = f"Group {self.group_idx + 1} un-ignored."

    def reset_current_group(self):
        """Reset deletion and ignore state for current group."""
        if not self.groups:
            return
        g = self.groups[self.group_idx]
        state = self.group_states[g.full_hash]
        state['deleted_ids'].clear()
        state['is_ignored'] = False
        self.status_message = f"Reset group {self.group_idx + 1}."

    def toggle_current_file(self):
        """Toggle delete state for current file in current group."""
        if not self.groups:
            return
        g = self.groups[self.group_idx]
        if not g.files:
            return
        state = self.group_states[g.full_hash]
        if state['is_ignored']:
            self.status_message = "Group is ignored. Press [i] to un-ignore before selecting files."
            return
            
        file_to_toggle = g.files[self.file_idx]
        fid = file_to_toggle.file_id
        
        if fid in state['deleted_ids']:
            state['deleted_ids'].remove(fid)
            self.status_message = f"Kept: [{file_to_toggle.volume_display_name}] {file_to_toggle.relative_path}"
        else:
            # Check safety: cannot delete ALL files
            if len(state['deleted_ids']) + 1 >= len(g.files):
                self.status_message = "Cannot delete ALL copies! At least one copy must be kept."
                try:
                    curses.beep()
                except Exception:
                    pass
                return
            state['deleted_ids'].add(fid)
            self.status_message = f"Marked for deletion: [{file_to_toggle.volume_display_name}] {file_to_toggle.relative_path}"

    def get_summary_stats(self) -> tuple[int, int, int]:
        """Returns (files_to_delete_count, total_space_saved, ignored_count)."""
        del_count = 0
        space_saved = 0
        ignored_count = 0
        for g in self.groups:
            state = self.group_states[g.full_hash]
            if state['is_ignored']:
                ignored_count += 1
            else:
                del_ids = state['deleted_ids']
                del_count += len(del_ids)
                space_saved += g.file_size * len(del_ids)
        return del_count, space_saved, ignored_count

    def build_plan(self) -> tuple[CleanupPlan, list[str]]:
        """Build CleanupPlan and list of ignored hashes."""
        plan = CleanupPlan()
        ignored_hashes = []
        
        for g in self.groups:
            state = self.group_states[g.full_hash]
            if state['is_ignored']:
                ignored_hashes.append(g.full_hash)
                continue
                
            del_ids = state['deleted_ids']
            if not del_ids:
                continue
                
            # Pick first non-deleted file as reference to keep
            kept_orig = next(f for f in g.files if f.file_id not in del_ids)
            f_keep = FileToKeep(
                file_id=kept_orig.file_id,
                volume_id=kept_orig.volume_id,
                volume_label=kept_orig.volume_label,
                relative_path=kept_orig.relative_path,
                last_drive_letter=kept_orig.last_drive_letter
            )
            
            for f in g.files:
                if f.file_id in del_ids:
                    f_del = FileToDelete(
                        file_id=f.file_id,
                        volume_id=f.volume_id,
                        volume_label=f.volume_label,
                        relative_path=f.relative_path,
                        file_size=f.file_size,
                        last_drive_letter=f.last_drive_letter
                    )
                    plan.actions.append(CleanupAction(
                        file_to_delete=f_del,
                        file_to_keep=f_keep,
                        full_hash=g.full_hash
                    ))
                    
        return plan, ignored_hashes

    def run(self, stdscr) -> tuple[CleanupPlan, list[str]]:
        """Main curses event loop."""
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.use_default_colors()
        
        # Color pairs setup
        if curses.has_colors():
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)    # Header
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Active cursor
            curses.init_pair(3, curses.COLOR_RED, -1)                    # Delete tag
            curses.init_pair(4, curses.COLOR_GREEN, -1)                  # Keep tag
            curses.init_pair(5, curses.COLOR_YELLOW, -1)                 # Accent/Keys
            curses.init_pair(6, curses.COLOR_MAGENTA, -1)                # Hash / size
            curses.init_pair(7, curses.COLOR_CYAN, -1)                   # Subtitle
        
        while True:
            max_y, max_x = stdscr.getmaxyx()
            if max_y < 12 or max_x < 60:
                stdscr.clear()
                msg = "Please resize terminal (min 60x12)..."
                try:
                    stdscr.addstr(max_y // 2, max(0, (max_x - len(msg)) // 2), msg)
                except curses.error:
                    pass
                stdscr.refresh()
                ch = stdscr.getch()
                if ch in (ord('q'), ord('Q'), 27):
                    return CleanupPlan(), []
                continue

            self._draw(stdscr, max_y, max_x)
            
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                return CleanupPlan(), []

            if ch in (ord('q'), ord('Q'), 27):  # 'q' or Esc
                return CleanupPlan(), []

            elif ch in (ord('\n'), ord('\r'), ord('c'), ord('C')):  # Enter or 'c'
                return self.build_plan()

            elif ch in (9,):  # Tab
                self.active_panel = 1 - self.active_panel
                self.status_message = "Switched to " + ("Files panel" if self.active_panel == 1 else "Groups panel")

            elif ch in (curses.KEY_LEFT, ord('h')):
                if self.active_panel == 1 and self.file_hscroll > 0:
                    self.file_hscroll = max(0, self.file_hscroll - 8)
                else:
                    self.active_panel = 0
                    self.file_hscroll = 0

            elif ch in (curses.KEY_RIGHT, ord('l')):
                if self.active_panel == 0:
                    self.active_panel = 1
                    self.file_hscroll = 0
                else:
                    self.file_hscroll += 8

            elif ch in (curses.KEY_HOME, ord('0')):
                self.file_hscroll = 0

            elif ch in (curses.KEY_UP, ord('k')):
                self._handle_up()

            elif ch in (curses.KEY_DOWN, ord('j')):
                self._handle_down()

            elif ch in (curses.KEY_PPAGE,):  # Page Up
                for _ in range(5):
                    self._handle_up()

            elif ch in (curses.KEY_NPAGE,):  # Page Down
                for _ in range(5):
                    self._handle_down()

            elif ch == ord(' '):  # Space toggle
                if self.active_panel == 1:
                    self.toggle_current_file()
                else:
                    # In groups panel, space toggles auto-selection
                    g = self.groups[self.group_idx]
                    state = self.group_states[g.full_hash]
                    if state['deleted_ids']:
                        self.reset_current_group()
                    else:
                        self.auto_select_group(g)

            elif ch in (ord('a'),):  # Auto current
                if self.groups:
                    self.auto_select_group(self.groups[self.group_idx])

            elif ch in (ord('A'),):  # Auto all
                self.auto_select_all()

            elif ch in (ord('i'), ord('I')):  # Ignore current
                self.toggle_ignore_current()

            elif ch in (ord('s'), ord('S'), ord('r'), ord('R')):  # Skip / Reset current
                self.reset_current_group()

    def _handle_up(self):
        self.file_hscroll = 0
        if self.active_panel == 0:
            if self.group_idx > 0:
                self.group_idx -= 1
                self.file_idx = 0
                self.file_scroll = 0
        else:
            if self.file_idx > 0:
                self.file_idx -= 1

    def _handle_down(self):
        self.file_hscroll = 0
        if self.active_panel == 0:
            if self.group_idx < len(self.groups) - 1:
                self.group_idx += 1
                self.file_idx = 0
                self.file_scroll = 0
        else:
            g = self.groups[self.group_idx]
            if self.file_idx < len(g.files) - 1:
                self.file_idx += 1

    def _draw(self, stdscr, max_y: int, max_x: int):
        stdscr.erase()
        
        # 1. Header (Line 0)
        del_count, space_saved, ignored_count = self.get_summary_stats()
        header_text = (
            f" Redundant Files Cleanup | Groups: {len(self.groups)} "
            f"| To Delete: {del_count} files ({format_size(space_saved)}) "
            f"| Ignored: {ignored_count} "
        )
        header_text = header_text[:max_x - 1].ljust(max_x - 1)
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE)
            stdscr.addstr(0, 0, header_text)
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE)
        except curses.error:
            pass

        # Calculate panel boundaries (give ~72-76% of width to files panel)
        split_x = min(28, max(24, int(max_x * 0.28)))
        content_y_start = 1
        content_y_end = max_y - 5  # Leave 2 lines for file details, 1 for status, 2 for keys footer
        panel_height = content_y_end - content_y_start

        # 2. Draw Panels Separator & Titles
        left_title = " Duplicate Groups "
        right_title = " Files in Group "
        if self.groups:
            g = self.groups[self.group_idx]
            right_title = f" Group {self.group_idx + 1}/{len(self.groups)} (Hash: {g.full_hash[:8]}, Size: {format_size(g.file_size)}) "

        try:
            # Top subtitle line
            stdscr.addstr(content_y_start, 1, left_title[:split_x - 2], curses.A_BOLD)
            stdscr.addstr(content_y_start, split_x + 1, right_title[:max_x - split_x - 2], curses.A_BOLD)
            
            # Vertical separator
            for y in range(content_y_start, content_y_end):
                stdscr.addch(y, split_x, '|')
        except curses.error:
            pass

        content_rows = panel_height - 1
        list_y_start = content_y_start + 1

        # 3. Draw Left Panel (Groups)
        if self.group_idx < self.group_scroll:
            self.group_scroll = self.group_idx
        elif self.group_idx >= self.group_scroll + content_rows:
            self.group_scroll = self.group_idx - content_rows + 1

        for i in range(content_rows):
            idx = self.group_scroll + i
            if idx >= len(self.groups):
                break
            g = self.groups[idx]
            state = self.group_states[g.full_hash]
            
            if state['is_ignored']:
                tag = "[IGN]"
                tag_attr = curses.color_pair(5) if curses.has_colors() else curses.A_NORMAL
            elif state['deleted_ids']:
                tag = f"[{len(state['deleted_ids'])} del]"
                tag_attr = curses.color_pair(3) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
            else:
                tag = "[skip]"
                tag_attr = curses.color_pair(4) if curses.has_colors() else curses.A_DIM

            is_current = (idx == self.group_idx)
            cursor = ">" if is_current else " "
            label = f"{cursor}{idx + 1:2d}. {format_size(g.file_size):>8} ({g.file_count}) {tag}"
            label = label[:split_x - 1].ljust(split_x - 1)

            row_y = list_y_start + i
            try:
                if is_current:
                    if self.active_panel == 0:
                        attr = curses.color_pair(2) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE
                    else:
                        attr = curses.A_REVERSE
                    stdscr.addstr(row_y, 0, label, attr)
                else:
                    stdscr.addstr(row_y, 0, label[:split_x - len(tag) - 1])
                    stdscr.addstr(row_y, split_x - len(tag) - 1, tag, tag_attr)
            except curses.error:
                pass

        # 4. Draw Right Panel (Files in selected group)
        if self.groups:
            cur_group = self.groups[self.group_idx]
            cur_state = self.group_states[cur_group.full_hash]
            files = cur_group.files
            
            # Ensure file scroll follows cursor
            visible_files_capacity = max(1, content_rows // 2)
            if self.file_idx < self.file_scroll:
                self.file_scroll = self.file_idx
            elif self.file_idx >= self.file_scroll + visible_files_capacity:
                self.file_scroll = max(0, self.file_idx - visible_files_capacity + 1)

            for i in range(visible_files_capacity):
                f_idx = self.file_scroll + i
                if f_idx >= len(files):
                    break
                f = files[f_idx]
                is_del = f.file_id in cur_state['deleted_ids']
                is_cur_file = (f_idx == self.file_idx)
                
                check = "[*] (DEL) " if is_del else "[ ] (KEEP)"
                tag_color = curses.color_pair(3) | curses.A_BOLD if is_del else curses.color_pair(4) | curses.A_BOLD
                if not curses.has_colors():
                    tag_color = curses.A_BOLD if is_del else curses.A_NORMAL

                cursor_mark = ">" if is_cur_file else " "
                
                # Extract filename and parent folder cleanly
                from pathlib import Path
                p = Path(f.relative_path)
                filename = p.name
                parent_dir = str(p.parent).replace('\\', '/')
                dir_label = parent_dir if parent_dir != '.' else '(root)'
                
                name_display = filename
                if self.file_hscroll > 0 and is_cur_file:
                    if self.file_hscroll < len(name_display):
                        name_display = f"[+{self.file_hscroll}] " + name_display[self.file_hscroll:]

                line1 = f" {cursor_mark} {check} [{f.volume_display_name}] {name_display}"
                
                mod_dt = datetime.fromtimestamp(f.file_modified_at).strftime('%Y-%m-%d %H:%M')
                line2 = f"       Vol: {f.volume_display_name} | Folder: {dir_label} | {format_size(f.file_size)} | {mod_dt}"
                
                right_w = max_x - split_x - 2
                line1_fmt = line1[:right_w].ljust(right_w)
                line2_fmt = line2[:right_w].ljust(right_w)

                row_y1 = list_y_start + (i * 2)
                row_y2 = row_y1 + 1

                try:
                    if is_cur_file and self.active_panel == 1:
                        highlight_attr = curses.color_pair(2) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE
                        stdscr.addstr(row_y1, split_x + 1, line1_fmt, highlight_attr)
                        stdscr.addstr(row_y2, split_x + 1, line2_fmt, highlight_attr)
                    else:
                        stdscr.addstr(row_y1, split_x + 1, line1_fmt)
                        tag_x = split_x + 4
                        stdscr.addstr(row_y1, tag_x, check, tag_color)
                        stdscr.addstr(row_y2, split_x + 1, line2_fmt, curses.A_DIM)
                except curses.error:
                    pass

        # 5. Dedicated Full Path Details Box (Lines max_y - 5 and max_y - 4)
        details_y1 = max_y - 5
        details_y2 = max_y - 4
        if self.groups and self.groups[self.group_idx].files:
            cur_group = self.groups[self.group_idx]
            cur_state = self.group_states[cur_group.full_hash]
            sel_f = cur_group.files[self.file_idx]
            is_del = sel_f.file_id in cur_state['deleted_ids']
            status_tag = "[MARKED FOR DELETION]" if is_del else "[WILL BE KEPT]"
            
            full_path_str = f" Full Path: [{sel_f.volume_display_name}] {sel_f.full_path}"
            if self.file_hscroll > 0 and len(full_path_str) > max_x - 1:
                offset_text = f" Full Path: [..+{self.file_hscroll}..] " + f"[{sel_f.volume_display_name}] {sel_f.full_path}"[self.file_hscroll:]
                full_path_str = offset_text
            full_path_str = full_path_str[:max_x - 1].ljust(max_x - 1)

            mod_full = datetime.fromtimestamp(sel_f.file_modified_at).strftime('%Y-%m-%d %H:%M:%S')
            meta_str = f" Status: {status_tag} | Volume: {sel_f.volume_display_name} | Size: {format_size(sel_f.file_size)} | Modified: {mod_full} | Hash: {cur_group.full_hash[:12]}"
            meta_str = meta_str[:max_x - 1].ljust(max_x - 1)

            try:
                # Separator line
                stdscr.addstr(details_y1 - 1, 0, ("─" * (max_x - 1))[:max_x - 1], curses.A_DIM)
                stdscr.addstr(details_y1, 0, full_path_str, curses.A_BOLD)
                status_color = curses.color_pair(3) | curses.A_BOLD if is_del else curses.color_pair(4) | curses.A_BOLD
                if not curses.has_colors():
                    status_color = curses.A_BOLD
                stdscr.addstr(details_y2, 0, meta_str, curses.A_DIM)
            except curses.error:
                pass

        # 6. Status line (Line max_y - 3)
        status_y = max_y - 3
        try:
            status_text = f" {self.status_message}"[:max_x - 1].ljust(max_x - 1)
            stdscr.addstr(status_y, 0, status_text, curses.color_pair(5) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD)
        except curses.error:
            pass

        # 7. Keys Help Footer (Bottom 2 lines)
        keys_line1 = " [↑/↓]:Navigate  [Tab]:Switch Panel  [←/→]:Pan/Scroll  [Space]:Toggle Del/Keep  [a]:Auto  [A]:Auto ALL"
        keys_line2 = " [i]:Ignore group  [s]:Reset group  [Enter/c]:Confirm & Execute  [q/Esc]:Cancel"
        
        try:
            stdscr.addstr(max_y - 2, 0, keys_line1[:max_x - 1].ljust(max_x - 1), curses.A_DIM)
            stdscr.addstr(max_y - 1, 0, keys_line2[:max_x - 1].ljust(max_x - 1), curses.A_DIM)
        except curses.error:
            pass

        stdscr.refresh()

def run_cleanup_tui(groups: list[DuplicateGroup]) -> tuple[CleanupPlan, list[str]]:
    """Run interactive full-screen curses cleanup selection."""
    tui = CleanupTUI(groups)
    return curses.wrapper(tui.run)
