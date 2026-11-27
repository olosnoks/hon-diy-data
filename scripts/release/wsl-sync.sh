# Sourced by the WSL build scripts (bash). Not run on its own.
#
# sync_tree SRC DST [rsync excludes...]
#   Copies a checkout that lives on the Windows drive (/mnt/c/...) into a native
#   Linux folder: builds on /mnt/c are slow and PlatformIO/PyInstaller trip over
#   it. Build folders on the destination (.pio, build, dist) survive between
#   runs so later builds are incremental. No .git is copied: pass the revision
#   with --rev if you want it recorded.

sync_tree() {
  local src="$1" dst="$2"; shift 2
  command -v rsync >/dev/null || { echo "rsync is missing: sudo apt-get install -y rsync" >&2; return 2; }
  mkdir -p "$dst"
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
    --exclude '/web' --exclude '/release' --exclude '/build' --exclude '/dist' \
    --exclude '/build-logs' --exclude '/firmware-out' --exclude '/docs/analysis' \
    --exclude '.pio' --exclude '/firmware/*/build' --exclude '/firmware/*/build-*' \
    "$@" "$src/" "$dst/"
}

# true when the path is on a Windows drive mounted into WSL
on_windows_drive() { case "$1" in /mnt/[a-z]/*) return 0 ;; *) return 1 ;; esac; }

# normalize_eol DIR [SRC]
#   A Windows checkout (core.autocrlf=true) has CRLF text files; bash scripts,
#   Makefiles and sdkconfig files break on them in Linux. Converts the text
#   types the repo holds back to LF (as git stores them). Binary files are
#   left alone: only these names are touched. With SRC (where DIR was copied
#   from) each file keeps the source's mtime, so incremental builds stay incremental.
normalize_eol() {
  local dir="$1" src="${2:-}" f rel
  find "$dir" \( -name .pio -o -name node_modules -o -name .git -o -name build \) -prune -o -type f \( \
      -name '*.sh' -o -name '*.py' -o -name '*.c' -o -name '*.h' -o -name '*.cpp' -o -name '*.txt' \
      -o -name '*.csv' -o -name '*.toml' -o -name '*.ini' -o -name '*.yml' -o -name '*.yaml' -o -name '*.json' \
      -o -name '*.md' -o -name '*.spec' -o -name '*.rules' -o -name '*.desktop' -o -name '*.svg' \
      -o -name '*.cmake' -o -name 'CMakeLists.txt' -o -name 'Makefile' -o -name '*.mk' -o -name 'sdkconfig*' \
      -o -name 'Kconfig*' -o -name '.gitignore' -o -name '*.ld' -o -name '*.cfg' \
    \) -print0 | xargs -0 -r grep -lZ $'\r' 2>/dev/null |
  while IFS= read -r -d '' f; do
    sed -i 's/\r$//' "$f"
    rel="${f#"$dir"/}"
    if [ -n "$src" ] && [ -e "$src/$rel" ]; then touch -r "$src/$rel" "$f"; fi
  done
  return 0
}
