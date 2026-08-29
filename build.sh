#!/usr/bin/env bash
# Builda e publica a imagem no Docker Hub (cybernetus/agy-gateway-ng),
# tageada com a versão de main.py (__version__) e :latest.
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="cybernetus/agy-gateway-ng"
VERSION=$(grep -oP '__version__ = "\K[^"]+' main.py)

if [ -z "$VERSION" ]; then
    echo "erro: não consegui extrair __version__ de main.py" >&2
    exit 1
fi

echo "==> build ${IMAGE}:${VERSION}"
docker build -t "${IMAGE}:${VERSION}" -t "${IMAGE}:latest" .

echo "==> push ${IMAGE}:${VERSION}"
docker push "${IMAGE}:${VERSION}"

echo "==> push ${IMAGE}:latest"
docker push "${IMAGE}:latest"

echo "==> ok: ${IMAGE}:${VERSION} (e :latest) publicado"
