#!/usr/bin/env sh
set -eu

# Sync a local custom-component source to a Home Assistant host over SSH.
#
# Usage:
#   script/sync_custom_component.sh HOST COMPONENT [USER] [--no-restart] [--dry-run]
#
#   HOST       Home Assistant host (IP or hostname), required.
#   COMPONENT  Directory name under homeassistant/components/, required.
#   USER       SSH user, optional (default: root).

HOST=""
COMPONENT=""
USER="root"
RESTART=1
DRY_RUN=0
POSITIONALS=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-restart) RESTART=0 ;;
        --dry-run | -n) DRY_RUN=1 ;;
        -*) echo "Unknown argument: $1" >&2; exit 2 ;;
        *)
            POSITIONALS=$((POSITIONALS + 1))
            case "$POSITIONALS" in
                1) HOST=$1 ;;
                2) COMPONENT=$1 ;;
                3) USER=$1 ;;
                *) echo "Too many arguments: $1" >&2; exit 2 ;;
            esac
            ;;
    esac
    shift
done

if [ -z "$HOST" ] || [ -z "$COMPONENT" ]; then
    echo "Usage: $0 HOST COMPONENT [USER] [--no-restart] [--dry-run]" >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_DIR="$REPO_ROOT/homeassistant/components/$COMPONENT"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "No such component directory: $SOURCE_DIR" >&2
    exit 1
fi

REMOTE="$USER@$HOST"
REMOTE_DIR="/config/custom_components/$COMPONENT"

# Mirror the source into the remote dir over SSH using tar (HassOS lacks rsync).
if [ "$DRY_RUN" -eq 1 ]; then
    tar -C "$SOURCE_DIR" --exclude strings.json --exclude __pycache__ --exclude .pytest_cache -cf - . | tar -tf -
    exit 0
fi

ssh "$REMOTE" "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR'"
tar -C "$SOURCE_DIR" --exclude strings.json --exclude __pycache__ --exclude .pytest_cache -cf - . | ssh "$REMOTE" "tar -C '$REMOTE_DIR' -xf -"

if [ "$RESTART" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
    ssh "$REMOTE" 'ha core restart'

    # Wait for Home Assistant to come back up, then surface startup output.
    sleep 5
    i=0
    while [ "$i" -lt 30 ]; do
        if ssh "$REMOTE" 'ha core info' 2>/dev/null | grep -q 'state: started'; then
            break
        fi
        i=$((i + 1))
        sleep 2
    done

    ssh "$REMOTE" 'ha core info'
    ssh "$REMOTE" "ha core logs --lines 300 2>&1 | grep -i -E \"$COMPONENT|blocked|error\"" || true
fi
