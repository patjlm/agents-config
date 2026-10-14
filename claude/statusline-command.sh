#!/usr/bin/env bash
# Claude Code status line: ctx | dir | git info

# ANSI color helpers (bash ANSI-C quoting so escapes are real characters)
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
CYAN=$'\033[36m'
YELLOW=$'\033[33m'
GREEN=$'\033[32m'
RED=$'\033[31m'

input=$(cat)

# --- Current directory ---
cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // empty')
case "$cwd" in
  "$HOME"/*) short_cwd="~/${cwd#"$HOME"/}" ;;
  "$HOME")   short_cwd="~" ;;
  *)         short_cwd="$cwd" ;;
esac

# --- Git info via __git_ps1 ---
git_part=""
if [ -f /usr/share/git-core/contrib/completion/git-prompt.sh ]; then
  # shellcheck source=/usr/share/git-core/contrib/completion/git-prompt.sh
  source /usr/share/git-core/contrib/completion/git-prompt.sh

  export GIT_PS1_SHOWDIRTYSTATE=1
  export GIT_PS1_SHOWUNTRACKEDFILES=1
  export GIT_PS1_SHOWUPSTREAM="verbose name"
  export GIT_PS1_SHOWSTASHSTATE=1

  # Run __git_ps1 in the context of $cwd
  raw_git=$(cd "$cwd" 2>/dev/null && __git_ps1 "%s" 2>/dev/null)

  if [ -n "$raw_git" ]; then
    # Color dirty markers and upstream indicators
    colored_git="$raw_git"
    # upstream verbose: u= green, u+N green, u-N red, u+N-M red
    colored_git=$(echo "$colored_git" | sed \
      -e "s/u=/${GREEN}u=${RESET}/g" \
      -e "s/u+\([0-9]*\)-\([0-9]*\)/${RED}u+\1-\2${RESET}/g" \
      -e "s/u+\([0-9]*\)/${GREEN}u+\1${RESET}/g" \
      -e "s/u-\([0-9]*\)/${RED}u-\1${RESET}/g")
    # dirty: * + % red
    colored_git="${colored_git//\*/${RED}*${RESET}}"
    colored_git="${colored_git//+/${RED}+${RESET}}"
    colored_git="${colored_git//%/${RED}%${RESET}}"
    # $ (stash) yellow
    colored_git="${colored_git//\$/${YELLOW}\$${RESET}}"

    git_part=" | ${colored_git}"
  fi
fi

# --- Model ---
model_name=$(echo "$input" | jq -r '.model.display_name // empty')

# --- Context info ---
ctx_part=""
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
total_in=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
if [ -n "$used" ] && [ -n "$total_in" ]; then
  total_in_k=$(awk "BEGIN { printf \"%.0f\", $total_in/1000 }")
  if [ "${total_in}" -ge 200000 ] 2>/dev/null; then
    ctx_color="$RED"
  elif [ "${total_in}" -ge 150000 ] 2>/dev/null; then
    ctx_color="$YELLOW"
  else
    ctx_color="$DIM"
  fi
  ctx_part="${ctx_color}${total_in_k}k $(printf '%.0f' "$used")%${RESET}"
fi

# Line 1: path | git
printf '%s%s%s%s\n' "${BOLD}${CYAN}" "$short_cwd" "${RESET}" "$git_part"

# Line 2: ctx | model
line2=""
[ -n "$ctx_part" ] && line2="$ctx_part"
[ -n "$model_name" ] && line2="${line2}${line2:+ | }${DIM}${model_name}${RESET}"
[ -n "$line2" ] && printf '%s' "$line2"
