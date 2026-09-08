#!/bin/sh
# Docker builds a native helper; the host browser owns the loopback callback.
set -eu
cd "$(dirname "$0")/.."
case "${FORGE_LOCAL_HELPER:-demo}" in
  demo) client_binary=forgeapi-demo ;;
  keyvault-worker) client_binary=forgeapi-keyvault-worker ;;
  *) echo 'Only demo and keyvault-worker helpers are supported.' >&2; exit 1 ;;
esac
case "${1:-}" in
  -h|-help|--help) ;;
  *) test -f .env || { echo 'Complete docs/entra-local.md first (.env is missing).' >&2; exit 1; } ;;
esac
case "$(uname -s)" in
  Darwin) client_os=darwin ;;
  Linux) client_os=linux ;;
  *) echo 'This helper supports macOS and Linux.' >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) client_arch=arm64 ;;
  x86_64) client_arch=amd64 ;;
  *) echo 'Unsupported host architecture.' >&2; exit 1 ;;
esac
docker build --target demo-build -t forgeapi-demo-build:local \
  --build-arg "CLIENT_OS=$client_os" --build-arg "CLIENT_ARCH=$client_arch" \
  .
# Copy the built host executable from a never-started container. Works with
# Docker's classic builder too; no extra Buildx installation is required.
mkdir -p .local/bin
client_container=$(docker create --network none forgeapi-demo-build:local)
trap 'docker rm "$client_container" >/dev/null' 0
docker cp "$client_container:/out/$client_binary" ".local/bin/$client_binary"
docker rm "$client_container" >/dev/null
trap - 0
exec "./.local/bin/$client_binary" "$@"
