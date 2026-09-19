#!/usr/bin/env bash
# Fetch the GGUF weights Naka runs on. Idempotent: skips files already present
# and intact, resumes partial downloads.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p .models

# repo|filename|sha256
MODELS=(
  "Qwen/Qwen3-14B-GGUF|Qwen3-14B-Q4_K_M.gguf|500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0"
  "google/gemma-4-12B-it-qat-q4_0-gguf|gemma-4-12b-it-qat-q4_0.gguf|93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b"
)

for entry in "${MODELS[@]}"; do
  IFS='|' read -r repo file want <<<"$entry"
  path=".models/$file"

  if [[ -f "$path" ]] && [[ "$(sha256sum "$path" | cut -d' ' -f1)" == "$want" ]]; then
    echo "ok       $file"
    continue
  fi

  echo "fetching $file"
  curl -fL -C - -o "$path" "https://huggingface.co/$repo/resolve/main/$file"

  got=$(sha256sum "$path" | cut -d' ' -f1)
  if [[ "$got" != "$want" ]]; then
    echo "checksum mismatch for $file: got $got, want $want" >&2
    exit 1
  fi
  echo "ok       $file"
done

echo
echo "Models ready. Start the LLM server with:"
echo "  docker compose up -d llm                                      # gemma (default)"
echo "  MODEL=Qwen3-14B-Q4_K_M.gguf docker compose up -d llm          # qwen"
