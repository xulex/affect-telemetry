"""
task_checklist.py
=================

Floating always-on-top window showing the four task blocks and the session
countdown. Designed to sit unobtrusively in a corner of the participant's
screen while they work in Excel, Mail, browser, etc.

Each task block has a checkbox the participant clicks when they consider
that task done. Clicks are logged as task_completed events with timestamps
into <session_dir>/task_checklist.jsonl.

Reads the session start time from session_metadata.json in the session
directory it's pointed at. If the file isn't found, falls back to
"now" as the session start.

USAGE
-----
    python task_checklist.py SESSION_DIR --duration 1560 --language en

For testing without a real session dir:
    python task_checklist.py /tmp/test --duration 120 --language en
"""

import os
import sys
import json
import time
import signal
import argparse
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk


# =============================================================================
# Visual constants (matches session_controller / survey_prompter style)
# =============================================================================

COLOR_BG_WINDOW       = '#FFFFFF'
COLOR_BG_BUTTON       = '#F2F2F2'
COLOR_BG_DONE         = '#22C55E'
COLOR_BG_DONE_HOVER   = '#16A34A'
COLOR_FG_DONE         = '#FFFFFF'
COLOR_FG_BUTTON       = '#333333'
COLOR_BORDER          = '#CCCCCC'
COLOR_TEXT_TITLE      = '#222222'
COLOR_TEXT_BODY       = '#333333'
COLOR_TEXT_MUTED      = '#777777'
COLOR_TIMER_BG        = '#1F2937'
COLOR_TIMER_FG        = '#FFFFFF'
COLOR_TIMER_LOW_FG    = '#F87171'   # red when < 5 min remaining


# =============================================================================
# Localization
# =============================================================================

STRINGS = {
    'en': {
        'window_title': 'Tasks',
        'session_time_label': 'Session time left',
        'tasks': [
            ('read',      'Read the three documents',      5),
            ('analyse',   'Open Excel and analyse the data', 6),
            ('draft',     'Draft your email to Carla',     6),
            ('refine',    'Refine, attach files, hit Send', 4),
        ],
        'min_label': 'min',
        'completed_label': 'done',
    },
    'pt': {
        'window_title': 'Tarefas',
        'session_time_label': 'Tempo restante',
        'tasks': [
            ('read',      'Ler os três documentos',           5),
            ('analyse',   'Abrir o Excel e analisar os dados', 6),
            ('draft',     'Escrever o email à Carla',         6),
            ('refine',    'Refinar, anexar ficheiros, enviar', 4),
        ],
        'min_label': 'min',
        'completed_label': 'feito',
    },
}


# =============================================================================
# Helpers
# =============================================================================

def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path, obj):
    """Append a single JSON object to a JSONL file with fsync."""
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def read_session_start_utc(session_dir):
    """Read session_start_utc from session_metadata.json if present."""
    meta_path = session_dir / 'session_metadata.json'
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
        return meta.get('session_start_utc')
    except Exception:
        return None


# =============================================================================
# Task row widget
# =============================================================================

class TaskRow:
    """A single task row: checkbox + label, click to toggle done."""

    def __init__(self, parent, task_key, label_text, suggested_min,
                 on_toggle):
        self.task_key = task_key
        self.is_done = False
        self.on_toggle = on_toggle

        self.frame = tk.Frame(parent, bg=COLOR_BG_WINDOW, cursor='hand2')
        self.frame.pack(fill='x', padx=10, pady=6)

        # Checkbox cell
        self.checkbox = tk.Frame(
            self.frame, width=24, height=24,
            bg=COLOR_BG_BUTTON,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            cursor='hand2',
        )
        self.checkbox.pack_propagate(False)
        self.checkbox.pack(side='left', padx=(0, 10))
        self.checkmark = tk.Label(
            self.checkbox, text='',
            font=('Helvetica', 13, 'bold'),
            bg=COLOR_BG_BUTTON, fg=COLOR_FG_DONE,
            cursor='hand2',
        )
        self.checkmark.pack(expand=True, fill='both')

        # Label
        self.label = tk.Label(
            self.frame, text=label_text,
            font=('Helvetica', 11),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            cursor='hand2', anchor='w', justify='left',
            wraplength=210,
        )
        self.label.pack(side='left', fill='x', expand=True)

        # Suggested time pill
        self.time_label = tk.Label(
            self.frame, text=f'{suggested_min} min',
            font=('Helvetica', 9, 'italic'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
        )
        self.time_label.pack(side='right')

        # Click bindings (cover the whole row)
        for w in (self.frame, self.checkbox, self.checkmark,
                  self.label, self.time_label):
            w.bind('<Button-1>', self._on_click)

    def _on_click(self, _e):
        self.is_done = not self.is_done
        self._refresh_visual()
        self.on_toggle(self.task_key, self.is_done)

    def _refresh_visual(self):
        if self.is_done:
            self.checkbox.config(bg=COLOR_BG_DONE,
                                  highlightbackground=COLOR_BG_DONE)
            self.checkmark.config(bg=COLOR_BG_DONE, text='✓')
            self.label.config(fg=COLOR_TEXT_MUTED,
                              font=('Helvetica', 11, 'overstrike'))
        else:
            self.checkbox.config(bg=COLOR_BG_BUTTON,
                                  highlightbackground=COLOR_BORDER)
            self.checkmark.config(bg=COLOR_BG_BUTTON, text='')
            self.label.config(fg=COLOR_TEXT_BODY,
                              font=('Helvetica', 11))


# =============================================================================
# Main widget
# =============================================================================

class TaskChecklistWidget:
    def __init__(self, session_dir, duration_sec, language):
        self.session_dir = Path(session_dir).resolve()
        self.duration_sec = duration_sec
        self.language = language
        self.log_path = self.session_dir / 'task_checklist.jsonl'

        # Determine session start. Prefer session_metadata.json's value so
        # the countdown stays in sync with the main controller even if the
        # widget is launched a few seconds later.
        start_iso = read_session_start_utc(self.session_dir)
        if start_iso:
            try:
                self.session_start_t = datetime.fromisoformat(start_iso).timestamp()
            except ValueError:
                self.session_start_t = time.time()
        else:
            self.session_start_t = time.time()

        # Log widget_start
        if self.session_dir.is_dir():
            append_jsonl(self.log_path, {
                'type': 'widget_start',
                'timestamp_utc': now_utc_iso(),
                'session_dir': str(self.session_dir),
                'duration_sec': self.duration_sec,
                'language': self.language,
                'session_start_t_used': self.session_start_t,
            })

        # Build the window
        self.root = tk.Tk()
        s = STRINGS[self.language]
        self.root.title(s['window_title'])
        self.root.configure(bg=COLOR_BG_WINDOW)
        self.root.attributes('-topmost', True)
        # Disable close button — the widget should stay open the whole session
        self.root.protocol('WM_DELETE_WINDOW', lambda: None)
        # Compact size
        self.root.geometry('320x340')

        # Header: timer
        timer_frame = tk.Frame(self.root, bg=COLOR_TIMER_BG)
        timer_frame.pack(fill='x')
        tk.Label(
            timer_frame, text=s['session_time_label'],
            font=('Helvetica', 9),
            bg=COLOR_TIMER_BG, fg=COLOR_TEXT_MUTED,
        ).pack(pady=(8, 0))
        self.timer_label = tk.Label(
            timer_frame, text='--:--',
            font=('Helvetica', 28, 'bold'),
            bg=COLOR_TIMER_BG, fg=COLOR_TIMER_FG,
        )
        self.timer_label.pack(pady=(0, 8))

        # Task rows
        self.rows = []
        for task_key, label_text, suggested_min in s['tasks']:
            row = TaskRow(self.root, task_key, label_text, suggested_min,
                           self._on_task_toggle)
            self.rows.append(row)

        # Position window in top-right corner of screen
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        ww = self.root.winfo_width()
        self.root.geometry(f'+{sw - ww - 20}+40')

        # Kick off the timer
        self._tick()

    def _on_task_toggle(self, task_key, is_done):
        elapsed = time.time() - self.session_start_t
        record = {
            'type': 'task_toggle',
            'timestamp_utc': now_utc_iso(),
            'session_elapsed_sec': round(elapsed, 1),
            'task_key': task_key,
            'is_done': is_done,
        }
        if self.session_dir.is_dir():
            append_jsonl(self.log_path, record)

    def _tick(self):
        elapsed = time.time() - self.session_start_t
        remaining = max(0, self.duration_sec - elapsed)
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        self.timer_label.config(text=f'{mins:02d}:{secs:02d}')
        # Visual cue when low: red when under 5 min
        if remaining < 5 * 60 and remaining > 0:
            self.timer_label.config(fg=COLOR_TIMER_LOW_FG)
        elif remaining <= 0:
            self.timer_label.config(fg=COLOR_TIMER_LOW_FG, text='00:00')
        else:
            self.timer_label.config(fg=COLOR_TIMER_FG)

        # Continue ticking. Keep going past zero so the participant can see
        # they're in overtime. Don't auto-close — the session controller
        # decides when to end.
        self.root.after(500, self._tick)

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser(description='Floating task checklist widget.')
    ap.add_argument('session_dir', type=str,
                    help='Path to the active session directory. Used for log '
                         'output and to read session_start_utc from metadata.')
    ap.add_argument('--duration', type=int, default=1560,
                    help='Session duration in seconds (default 1560 = 26 min).')
    ap.add_argument('--language', type=str, default='en',
                    choices=('en', 'pt'),
                    help='Display language.')
    args = ap.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.exists():
        # Don't fail; create a temp directory for log output
        session_dir.mkdir(parents=True, exist_ok=True)

    widget = TaskChecklistWidget(session_dir=str(session_dir),
                                  duration_sec=args.duration,
                                  language=args.language)

    # Log a clean widget_end record on SIGTERM (from session_controller)
    # or SIGINT (Ctrl-C). Tk root.destroy() exits the mainloop cleanly.
    def handle_signal(signum, _frame):
        try:
            if session_dir.is_dir():
                append_jsonl(session_dir / 'task_checklist.jsonl', {
                    'type': 'widget_end',
                    'timestamp_utc': now_utc_iso(),
                    'reason': f'signal_{signum}',
                })
        except Exception:
            pass
        try:
            widget.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    widget.run()


if __name__ == '__main__':
    main()
