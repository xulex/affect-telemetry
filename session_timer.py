"""
session_timer.py

ASCII-art countdown timer for Phase 1 sessions. Displays a large block-character
clock that redraws in place using ANSI escape sequences.

Runs as a single long-lived process (no busy spawning) to avoid generating
process_events noise in osquery captures.

Usage:
    python session_timer.py --duration 300 --participant P03
"""

import argparse
import shutil
import signal
import sys
import time
from datetime import datetime, timezone


# 5-row block-character digit font. Each glyph is 5 rows tall, 5 columns wide.
GLYPHS = {
    '0': ['█████', '█   █', '█   █', '█   █', '█████'],
    '1': ['   ██', '  ███', '   ██', '   ██', '█████'],
    '2': ['█████', '    █', '█████', '█    ', '█████'],
    '3': ['█████', '    █', ' ████', '    █', '█████'],
    '4': ['█   █', '█   █', '█████', '    █', '    █'],
    '5': ['█████', '█    ', '█████', '    █', '█████'],
    '6': ['█████', '█    ', '█████', '█   █', '█████'],
    '7': ['█████', '    █', '   █ ', '  █  ', ' █   '],
    '8': ['█████', '█   █', '█████', '█   █', '█████'],
    '9': ['█████', '█   █', '█████', '    █', '█████'],
    ':': ['     ', '  ██ ', '     ', '  ██ ', '     '],
}


# ANSI escape sequences
CLEAR_SCREEN = '\033[2J'
CURSOR_HOME = '\033[H'
HIDE_CURSOR = '\033[?25l'
SHOW_CURSOR = '\033[?25h'
RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'

# Colors
CYAN = '\033[96m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
WHITE = '\033[97m'
BG_BLUE = '\033[44m'


def render_time(seconds: int) -> list:
    """Render MM:SS as 5 lines of ASCII art."""
    minutes = seconds // 60
    secs = seconds % 60
    text = f"{minutes:02d}:{secs:02d}"

    lines = ['', '', '', '', '']
    for ch in text:
        glyph = GLYPHS.get(ch, GLYPHS['0'])
        for row_idx, row in enumerate(glyph):
            lines[row_idx] += row + '  '
    return lines


def progress_bar(elapsed: int, total: int, width: int = 50) -> str:
    """Render a progress bar."""
    if total <= 0:
        return '█' * width
    filled = int(width * elapsed / total)
    filled = min(filled, width)
    return '█' * filled + '░' * (width - filled)


def color_for_remaining(remaining: int, total: int) -> str:
    """Choose color based on time remaining."""
    if total <= 0:
        return GREEN
    pct = remaining / total
    if pct > 0.5:
        return GREEN
    if pct > 0.25:
        return YELLOW
    return RED


def draw(start_time: float, duration: int, participant: str, session_id: str):
    """Draw the timer screen in place."""
    elapsed = int(time.monotonic() - start_time)
    remaining = max(0, duration - elapsed)
    color = color_for_remaining(remaining, duration)

    term_width = shutil.get_terminal_size((80, 24)).columns

    # Build output as one string to minimize flicker
    out = []
    out.append(CURSOR_HOME)
    out.append(CLEAR_SCREEN)

    # Header
    header = "  PHASE 1 SESSION  —  Recording in Progress  "
    pad = max(0, (term_width - len(header)) // 2)
    out.append('\n')
    out.append(' ' * pad + BG_BLUE + WHITE + BOLD + header + RESET + '\n')
    out.append('\n')

    # Metadata
    out.append(f"  {DIM}Participant:{RESET}  {BOLD}{participant}{RESET}\n")
    out.append(f"  {DIM}Session:{RESET}      {session_id}\n")
    out.append(f"  {DIM}Duration:{RESET}     {duration}s  ({duration // 60}m {duration % 60}s)\n")
    out.append('\n')

    # The countdown clock
    clock_lines = render_time(remaining)
    label = "remaining" if remaining > 0 else "done"
    for line in clock_lines:
        pad = max(0, (term_width - len(line)) // 2)
        out.append(' ' * pad + color + BOLD + line + RESET + '\n')
    out.append('\n')

    centered_label = label.upper()
    pad = max(0, (term_width - len(centered_label)) // 2)
    out.append(' ' * pad + DIM + centered_label + RESET + '\n')
    out.append('\n')

    # Progress bar
    bar_width = min(60, term_width - 20)
    bar = progress_bar(elapsed, duration, bar_width)
    pct = (elapsed / duration * 100) if duration > 0 else 100
    bar_pad = max(0, (term_width - bar_width - 8) // 2)
    out.append(' ' * bar_pad + color + bar + RESET + f"  {pct:>4.0f}%\n")
    out.append('\n')

    # Status line
    elapsed_str = f"{elapsed // 60:02d}:{elapsed % 60:02d} elapsed"
    pad = max(0, (term_width - len(elapsed_str)) // 2)
    out.append(' ' * pad + DIM + elapsed_str + RESET + '\n')
    out.append('\n')

    # Footer
    footer = "Press Ctrl-C to stop early"
    pad = max(0, (term_width - len(footer)) // 2)
    out.append(' ' * pad + DIM + footer + RESET + '\n')

    sys.stdout.write(''.join(out))
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, required=True,
                        help="Total session duration in seconds")
    parser.add_argument("--participant", type=str, default="self")
    parser.add_argument("--session-id", type=str, default="")
    args = parser.parse_args()

    session_id = args.session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Signal handling
    stop_flag = {"stop": False}

    def handle_signal(sig, frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Hide cursor for clean display
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()

    start = time.monotonic()
    try:
        while not stop_flag["stop"]:
            draw(start, args.duration, args.participant, session_id)
            elapsed = time.monotonic() - start
            if elapsed >= args.duration:
                break
            time.sleep(1)
        # One final draw to show completion
        draw(start, args.duration, args.participant, session_id)
        time.sleep(0.5)
    finally:
        # Restore cursor
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.write('\n')
        sys.stdout.flush()


if __name__ == "__main__":
    main()
