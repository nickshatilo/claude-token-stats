#!/usr/bin/env python3
"""
Multi-Timezone Digital Clock
A beautiful terminal-based digital clock displaying current time across multiple time zones.
"""

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones
from typing import List, Tuple

# ANSI Color Codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BRIGHT_BLACK = "\033[90m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


# Digital display segments (7-segment style)
DIGIT_SEGMENTS = {
    '0': ['█████', '█   █', '█   █', '█   █', '█████'],
    '1': ['    █', '    █', '    █', '    █', '    █'],
    '2': ['█████', '    █', '█████', '█    ', '█████'],
    '3': ['█████', '    █', '█████', '    █', '█████'],
    '4': ['█   █', '█   █', '█████', '    █', '    █'],
    '5': ['█████', '█    ', '█████', '    █', '█████'],
    '6': ['█████', '█    ', '█████', '█   █', '█████'],
    '7': ['█████', '    █', '    █', '    █', '    █'],
    '8': ['█████', '█   █', '█████', '█   █', '█████'],
    '9': ['█████', '█   █', '█████', '    █', '█████'],
    ':': ['  ', '██', '  ', '██', '  '],
}


def get_terminal_width() -> int:
    """Get the terminal width."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def clear_screen():
    """Clear the terminal screen."""
    os.system('clear' if os.name == 'posix' else 'cls')


def print_centered(text: str, color: str = "") -> None:
    """Print text centered in the terminal."""
    width = get_terminal_width()
    # Strip ANSI codes to get actual length
    clean_text = text.replace('\033[', '').split('m', 1)[-1] if '\033[' in text else text
    padding = max(0, (width - len(clean_text)) // 2)
    print(" " * padding + text)


def create_ascii_digit(digit: str, color: str) -> List[str]:
    """Create ASCII art for a single digit with color."""
    segments = DIGIT_SEGMENTS.get(digit, ['     '] * 5)
    return [f"{color}{seg}{Colors.RESET}" for seg in segments]


def create_ascii_time(time_str: str, color: str) -> List[str]:
    """Create ASCII art for the entire time string (HH:MM:SS format)."""
    lines = [''] * 5
    
    for char in time_str:
        digit_lines = create_ascii_digit(char, color)
        for i, line in enumerate(digit_lines):
            lines[i] += line + " "
    
    return lines


def get_timezones() -> List[Tuple[str, str]]:
    """Get a curated list of major timezones with display names."""
    timezones = [
        ('UTC', 'UTC (Coordinated Universal Time)'),
        ('America/New_York', 'EST - New York'),
        ('America/Chicago', 'CST - Chicago'),
        ('America/Denver', 'MST - Denver'),
        ('America/Los_Angeles', 'PST - Los Angeles'),
        ('Europe/London', 'GMT - London'),
        ('Europe/Paris', 'CET - Paris'),
        ('Europe/Moscow', 'MSK - Moscow'),
        ('Asia/Dubai', 'GST - Dubai'),
        ('Asia/Kolkata', 'IST - India'),
        ('Asia/Bangkok', 'ICT - Bangkok'),
        ('Asia/Hong_Kong', 'HKT - Hong Kong'),
        ('Asia/Tokyo', 'JST - Tokyo'),
        ('Asia/Seoul', 'KST - Seoul'),
        ('Australia/Sydney', 'AEDT - Sydney'),
        ('Pacific/Auckland', 'NZDT - Auckland'),
    ]
    return timezones


def format_timezone_clock(timezone: str, display_name: str, color: str) -> List[str]:
    """Format a single timezone clock display."""
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        time_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d, %Y")
        
        lines = []
        lines.append(f"{color}{'─' * 40}{Colors.RESET}")
        lines.append(f"{color}{display_name}{Colors.RESET}")
        
        # Add ASCII art time
        ascii_lines = create_ascii_time(time_str, color)
        lines.extend(ascii_lines)
        
        lines.append(f"{Colors.DIM}{date_str}{Colors.RESET}")
        lines.append("")
        
        return lines
    except Exception as e:
        return [f"{Colors.BRIGHT_RED}Error: {str(e)}{Colors.RESET}", ""]


def display_header() -> List[str]:
    """Display the header."""
    lines = []
    lines.append("")
    lines.append(f"{Colors.BRIGHT_CYAN}{Colors.BOLD}╔════════════════════════════════════════╗{Colors.RESET}")
    lines.append(f"{Colors.BRIGHT_CYAN}{Colors.BOLD}║   ⏰  MULTI-TIMEZONE DIGITAL CLOCK  ⏰  ║{Colors.RESET}")
    lines.append(f"{Colors.BRIGHT_CYAN}{Colors.BOLD}╚════════════════════════════════════════╝{Colors.RESET}")
    lines.append("")
    return lines


def display_timezone_grid(timezones: List[Tuple[str, str]], colors: List[str]) -> List[str]:
    """Display all timezones in a grid layout."""
    lines = []
    
    # Calculate columns based on terminal width
    width = get_terminal_width()
    cols = 2 if width >= 100 else 1
    
    if cols == 2:
        # Two-column layout
        for i in range(0, len(timezones), 2):
            left_tz, left_name = timezones[i]
            left_color = colors[i % len(colors)]
            left_lines = format_timezone_clock(left_tz, left_name, left_color)
            
            right_lines = []
            if i + 1 < len(timezones):
                right_tz, right_name = timezones[i + 1]
                right_color = colors[(i + 1) % len(colors)]
                right_lines = format_timezone_clock(right_tz, right_name, right_color)
            
            # Combine left and right columns
            max_lines = max(len(left_lines), len(right_lines))
            for j in range(max_lines):
                left = left_lines[j] if j < len(left_lines) else ""
                right = right_lines[j] if j < len(right_lines) else ""
                
                # Pad left side to align columns
                left_padded = left.ljust(40 + 20)  # Account for color codes
                lines.append(f"{left_padded}{right}")
    else:
        # Single column layout
        for i, (tz, name) in enumerate(timezones):
            color = colors[i % len(colors)]
            lines.extend(format_timezone_clock(tz, name, color))
    
    return lines


def display_footer() -> List[str]:
    """Display the footer with instructions."""
    lines = []
    lines.append(f"{Colors.DIM}Press Ctrl+C to exit{Colors.RESET}")
    lines.append(f"{Colors.DIM}Updates every second{Colors.RESET}")
    return lines


def main():
    """Main function to run the digital clock."""
    timezones = get_timezones()
    
    # Color cycle for different timezones
    colors = [
        Colors.BRIGHT_GREEN,
        Colors.BRIGHT_BLUE,
        Colors.BRIGHT_MAGENTA,
        Colors.BRIGHT_YELLOW,
        Colors.BRIGHT_CYAN,
        Colors.BRIGHT_RED,
    ]
    
    try:
        while True:
            clear_screen()
            
            # Display header
            output = display_header()
            
            # Display timezone grid
            output.extend(display_timezone_grid(timezones, colors))
            
            # Display footer
            output.append("")
            output.extend(display_footer())
            
            # Print all output
            print("\n".join(output))
            
            # Wait for next second
            time.sleep(1)
    
    except KeyboardInterrupt:
        clear_screen()
        print(f"{Colors.BRIGHT_GREEN}Thank you for using Multi-Timezone Clock!{Colors.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
