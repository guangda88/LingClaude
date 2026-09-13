#!/bin/bash

set -euo pipefail

TARGET_DIR="${1:-lingclaude}"

if [[ ! -d "$TARGET_DIR" ]]; then
    echo "0:0"
    exit 0
fi

find "$TARGET_DIR" -type f -name "*.py" ! -path "*/tests/*" | {
    file_count=0
    total_lines=0
    while IFS= read -r file; do
        [[ -f "$file" ]] || continue
        lines=$(wc -l < "$file")
        file_count=$((file_count + 1))
        total_lines=$((total_lines + lines))
    done
    echo "${file_count}:${total_lines}"
}