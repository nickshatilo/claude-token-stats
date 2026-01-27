#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${CYAN}"
echo "  ╭────────────────────────────────────────╮"
echo "  │   Claude Token Stats - Installation   │"
echo "  ╰────────────────────────────────────────╯"
echo -e "${NC}"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Directories
CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
STATS_DIR="$CLAUDE_DIR/stats"
LIB_DIR="$CLAUDE_DIR/lib"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

# Create directories
echo -e "${CYAN}Creating directories...${NC}"
mkdir -p "$HOOKS_DIR"
mkdir -p "$STATS_DIR"
mkdir -p "$LIB_DIR"

# Copy shared libraries
echo -e "${CYAN}Installing shared modules...${NC}"
cp "$SCRIPT_DIR/lib/pricing.py" "$LIB_DIR/pricing.py"
cp "$SCRIPT_DIR/lib/accounts.py" "$LIB_DIR/accounts.py"

# Copy hook script
echo -e "${CYAN}Installing hook script...${NC}"
cp "$SCRIPT_DIR/hooks/log-token-stats.py" "$HOOKS_DIR/log-token-stats.py"
chmod +x "$HOOKS_DIR/log-token-stats.py"

# Copy TUI viewer
echo -e "${CYAN}Installing stats viewer...${NC}"
cp "$SCRIPT_DIR/bin/claude-stats" "$HOOKS_DIR/claude-stats"
chmod +x "$HOOKS_DIR/claude-stats"

# Update settings.json
echo -e "${CYAN}Configuring Claude Code hooks...${NC}"

if [ -f "$SETTINGS_FILE" ]; then
    # Check if hooks already configured
    if grep -q '"PostToolUse"' "$SETTINGS_FILE" 2>/dev/null; then
        echo -e "${YELLOW}Hook already configured in settings.json${NC}"
    else
        # Add hooks to existing settings
        python3 << EOF
import json

settings_file = "$SETTINGS_FILE"
hooks_dir = "$HOOKS_DIR"

with open(settings_file, 'r') as f:
    settings = json.load(f)

settings['hooks'] = settings.get('hooks', {})
settings['hooks']['PostToolUse'] = [
    {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": f"python3 {hooks_dir}/log-token-stats.py"
            }
        ]
    }
]

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)

print("Settings updated successfully")
EOF
    fi
else
    # Create new settings file
    cat > "$SETTINGS_FILE" << EOF
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $HOOKS_DIR/log-token-stats.py"
          }
        ]
      }
    ]
  }
}
EOF
    echo -e "${GREEN}Created new settings.json${NC}"
fi

# Add shell alias
SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
    if ! grep -q 'alias claude-stats' "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# Claude Token Stats" >> "$SHELL_RC"
        echo "alias claude-stats=\"$HOOKS_DIR/claude-stats\"" >> "$SHELL_RC"
        echo -e "${GREEN}Added alias to $SHELL_RC${NC}"
    else
        echo -e "${YELLOW}Alias already exists in $SHELL_RC${NC}"
    fi
fi

echo ""
echo -e "${BOLD}${GREEN}Installation complete!${NC}"
echo ""
echo -e "${CYAN}Usage:${NC}"
echo -e "  ${BOLD}claude-stats${NC}    - Open interactive stats viewer"
echo ""
echo -e "${CYAN}Features:${NC}"
echo -e "  - Auto-logs token usage after each Claude response"
echo -e "  - Per-model breakdown (Opus, Sonnet, Haiku)"
echo -e "  - Input/Output/Cache tokens tracked separately"
echo -e "  - Multi-account support (API keys, orgs)"
echo -e "  - Interactive date range and account picker"
echo -e "  - Auto-refreshing display"
echo ""
echo -e "${YELLOW}Note:${NC} Restart your shell or run 'source $SHELL_RC' to use the alias"
echo -e "${YELLOW}Note:${NC} Start a new Claude Code session for hooks to take effect"
echo ""
