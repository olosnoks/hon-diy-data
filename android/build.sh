#!/usr/bin/env bash
# Build the standalone hondalog APK for the head unit: the web app (web/)
# bundled into the APK and run in the app's own WebView, with the native USB
# bridge for the KKL cable. No Gradle and no SDK download: Debian/Ubuntu
# packages only.
#
#   sudo apt-get install aapt apksigner dalvik-exchange android-sdk-platform-23 zipalign default-jdk-headless
#   (cd web && npm ci)          # once
#   android/build.sh            # -> android/build/hondalog-<version>-android-unsigned.apk
#                               #    and a test-signed copy you can install right away
#
# Sign the release copy with the real key (keeps updates installing in place):
#   Windows:  scripts\release\sign-apk.ps1 -Apk android\build\hondalog-<v>-android-unsigned.apk -Out hondalog-<v>-android.apk
#   Linux:    apksigner sign --ks ~/.hondalog-keys/hondalog-release.jks --ks-key-alias hondalog --out OUT.apk IN.apk
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
out="$here/build"
jar="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}"
dx="$(command -v dalvik-exchange || command -v dx || true)"

for t in aapt javac zipalign apksigner node; do
  command -v "$t" >/dev/null || { echo "missing: $t (see the apt-get line at the top)"; exit 1; }
done
[ -n "$dx" ] || { echo "missing: dalvik-exchange (apt-get install dalvik-exchange)"; exit 1; }
[ -f "$jar" ] || { echo "missing: $jar (apt-get install android-sdk-platform-23)"; exit 1; }

version="$(node -p "require('$repo/web/package.json').version")"
IFS=. read -r maj min pat <<<"${version%%-*}"
code=$(( maj * 10000 + min * 100 + pat ))   # 0.2.0 -> 200 (the old TWA was 1)
[ "$code" -gt 1 ] || code=2
echo "hondalog $version (versionCode $code)"

rm -rf "$out"
mkdir -p "$out/assets/www" "$out/gen" "$out/classes"

echo "== web build"
(cd "$repo/web" && HONDALOG_BASE=/ npx vite build --outDir "$out/web" --emptyOutDir --logLevel warn)
(cd "$out/web" && find . -type f ! -name '*.map' ! -name 'sw.js' -exec cp --parents {} "$out/assets/www/" \;)
cp "$here/assets/hondalog-android.js" "$out/assets/"

echo "== resources"
sed -e "s/@VERSION_CODE@/$code/" -e "s/@VERSION_NAME@/$version/" "$here/AndroidManifest.xml" > "$out/AndroidManifest.xml"
aapt package -f -M "$out/AndroidManifest.xml" -S "$here/res" -A "$out/assets" -I "$jar" \
  -m -J "$out/gen" -F "$out/resources.ap_" --ignore-assets '!.svn:!.git:.*:!CVS:*~' \
  -0 wasm -0 png -0 otf -0 ttf

echo "== java"
find "$here/src" -name '*.java' > "$out/sources.txt"
echo "$out/gen/com/olosnoks/hondalog/R.java" >> "$out/sources.txt"
javac -nowarn -Xlint:none -XDsuppressNotes -source 8 -target 8 -encoding UTF-8 -bootclasspath "$jar" -d "$out/classes" @"$out/sources.txt"
"$dx" --dex --min-sdk-version=23 --output="$out/classes.dex" "$out/classes"

echo "== apk"
cp "$out/resources.ap_" "$out/app.apk"
(cd "$out" && aapt add -f app.apk classes.dex >/dev/null)
unsigned="$out/hondalog-$version-android-unsigned.apk"
zipalign -f -p 4 "$out/app.apk" "$unsigned"

# A throwaway key so the build can be installed for a test straight away. It
# is NOT the release key: a test-signed APK can't update a release-signed one.
testks="$out/../.test-key.jks"
if [ ! -f "$testks" ]; then
  keytool -genkeypair -keystore "$testks" -storepass hondalogtest -keypass hondalogtest -alias test \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=hondalog test" >/dev/null 2>&1
fi
apksigner sign --ks "$testks" --ks-pass pass:hondalogtest --ks-key-alias test \
  --out "$out/hondalog-$version-android-TEST.apk" "$unsigned"

echo
ls -l "$unsigned" "$out/hondalog-$version-android-TEST.apk"
echo "Release: sign $unsigned with the hondalog release key (see the top of this script)."
