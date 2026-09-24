#!/bin/bash
# farmout installer.
#
#   curl -fsSL https://raw.githubusercontent.com/tp9imka/farmout/main/install.sh | bash
#   ./install.sh                 # from a clone: install that clone in place
#   ./install.sh --uninstall     # remove the links (and the clone it made)
#
# Environment:
#   FARMOUT_DIR      where to clone        (default ~/.local/share/farmout)
#   FARMOUT_BIN_DIR  where to link the CLI (default ~/.local/bin)
#   FARMOUT_REPO     git URL to clone      (default https://github.com/tp9imka/farmout)
#   FARMOUT_NO_SKILL=1  do not link the Claude Code skill
#
# It never installs system packages; missing tools are reported with a hint.
set -eu

REPO_URL="${FARMOUT_REPO:-https://github.com/tp9imka/farmout}"
DIR="${FARMOUT_DIR:-$HOME/.local/share/farmout}"
BIN_DIR="${FARMOUT_BIN_DIR:-$HOME/.local/bin}"
SKILL_LINK="$HOME/.claude/skills/farmout"

say()  { printf '\033[1;32mfarmout\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mfarmout\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mfarmout\033[0m %s\n' "$*" >&2; exit 1; }

# Run from inside a clone (./install.sh), not piped from curl.
here=""
case "${BASH_SOURCE[0]:-}" in
  */install.sh|install.sh) here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" ;;
esac
[ -n "$here" ] && [ -x "$here/bin/farmout" ] || here=""

uninstall() {
  local target
  if [ -L "$BIN_DIR/farmout" ]; then rm "$BIN_DIR/farmout"; say "removed $BIN_DIR/farmout"; fi
  if [ -L "$SKILL_LINK" ]; then
    target="$(readlink "$SKILL_LINK")"
    case "$target" in */skills/farmout) rm "$SKILL_LINK"; say "removed $SKILL_LINK" ;; esac
  fi
  if [ -d "$DIR/.git" ] && [ -f "$DIR/.farmout-installed" ]; then rm -rf "$DIR"; say "removed $DIR"; fi
  say "jobs and config are kept: ~/.cache/farmout, ~/.config/farmout (delete them by hand if you want)"
}

[ "${1:-}" = "--uninstall" ] && { uninstall; exit 0; }

# --- requirements ------------------------------------------------------------
missing=""
need() { command -v "$1" >/dev/null 2>&1 || missing="$missing $1"; }
need git; need jq; need python3; need perl
if [ -n "$missing" ]; then
  warn "missing:$missing"
  case "$(uname -s)" in
    Darwin) warn "install with: brew install$missing   (python3 also comes with Xcode Command Line Tools)" ;;
    Linux)  warn "install with your package manager, e.g. sudo apt install$missing" ;;
  esac
  die "install the missing tools, then run this again"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 >= 3.9 is required (found $(python3 --version 2>&1))"

# --- code --------------------------------------------------------------------
if [ -n "$here" ]; then
  DIR="$here"
  say "using this clone: $DIR"
elif [ -d "$DIR/.git" ]; then
  say "updating $DIR"
  git -C "$DIR" pull --ff-only --quiet || die "could not update $DIR (local changes?)"
else
  say "cloning $REPO_URL into $DIR"
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet --depth 1 "$REPO_URL" "$DIR"
  : > "$DIR/.farmout-installed"
fi

# --- CLI on PATH ---------------------------------------------------------------
mkdir -p "$BIN_DIR"
if [ -e "$BIN_DIR/farmout" ] && [ ! -L "$BIN_DIR/farmout" ]; then
  die "$BIN_DIR/farmout exists and is not a link; move it away and run again"
fi
ln -sfn "$DIR/bin/farmout" "$BIN_DIR/farmout"
say "linked $BIN_DIR/farmout"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH; add this to your shell profile:"
     warn "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# --- Claude Code skill ----------------------------------------------------------
if [ "${FARMOUT_NO_SKILL:-}" != 1 ] && [ -d "$HOME/.claude" ]; then
  mkdir -p "$HOME/.claude/skills"
  if [ -e "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
    warn "$SKILL_LINK exists and is not a link; skill not linked"
  else
    ln -sfn "$DIR/skills/farmout" "$SKILL_LINK"
    say "linked the Claude Code skill: $SKILL_LINK"
  fi
fi

# --- which workers are ready ------------------------------------------------------
say "checking agent CLIs (farmout doctor):"
"$DIR/bin/farmout" doctor || true
say "done - try: farmout help    dashboard: farmout arcade"
