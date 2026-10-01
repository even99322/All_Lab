#!/bin/bash
# Step 5 (macOS): disk image with the app and a shortcut to Applications.
#     packaging/make_dmg.sh <LabLogViewer.app> <output.dmg>
# hdiutil sometimes answers "Resource busy" while macOS is still scanning the new files:
# wait and try again (up to 6 times).
set -euo pipefail
APP="$1"; OUT="$2"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
for attempt in 1 2 3 4 5 6; do
  if hdiutil create -volname "LabLogViewer" -srcfolder "$STAGE" -ov -format UDZO "$OUT"; then
    rm -rf "$STAGE"
    echo "Disk image: $OUT"
    exit 0
  fi
  echo "hdiutil failed (attempt $attempt); retrying in $((attempt * 10)) s"
  sleep $((attempt * 10))
done
rm -rf "$STAGE"
exit 1
