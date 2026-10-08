#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Extract signed system-repository packages without installing system files.
# Keep all APT state local; restricted containers cannot switch to the _apt user.
mkdir -p .ocr/lists/partial .ocr/packages .ocr/cache/archives/partial
apt_options=(-o "Dir::State::lists=$PWD/.ocr/lists"
             -o "Dir::Cache=$PWD/.ocr/cache" -o "APT::Sandbox::User=$(id -un)")
apt-get "${apt_options[@]}" update
mapfile -t packages < <(apt-cache "${apt_options[@]}" depends --recurse \
  --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances \
  libtesseract5 liblept5 \
  | awk '/^[A-Za-z0-9][A-Za-z0-9+.-]*(:[A-Za-z0-9_-]+)?$/ {print $1}' | sort -u)
packages+=(tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng tesseract-ocr-osd fonts-dejavu-core)
if [ "${#packages[@]}" -eq 0 ]; then
  echo 'No OCR packages resolved from signed system repositories' >&2; exit 1
fi
(
  cd .ocr/packages
  apt-get "${apt_options[@]}" download "${packages[@]}"
  staging=$(mktemp -d ../extract.XXXXXX)
  trap 'rm -rf "$staging"' EXIT
  for package in "${packages[@]}"; do
    for pkg in "${package%%:*}"_*.deb; do dpkg-deb -x "$pkg" "$staging"; done
  done
  rm -rf ../usr
  mv "$staging/usr" ../usr
)
python document_ocr.py
touch .ocr/ready
