#!/bin/bash

set -euo pipefail

gcs() {
    git clone --depth=1 --no-tags --recurse-submodules --shallow-submodules "$@"
}

echo "########################################"
echo "[INFO] Downloading ComfyUI & Nodes..."
echo "########################################"

mkdir -p /default-comfyui-bundle
cd /default-comfyui-bundle
git clone 'https://github.com/comfyanonymous/ComfyUI.git'
cd /default-comfyui-bundle/ComfyUI
# Using stable version (has a release tag)
git reset --hard "$(git tag | grep -e '^v' | sort -V | tail -1)"

cd /default-comfyui-bundle/ComfyUI/custom_nodes
gcs https://github.com/Comfy-Org/ComfyUI-Manager.git

# Force ComfyUI-Manager to use PIP instead of UV
mkdir -p /default-comfyui-bundle/ComfyUI/user/default/ComfyUI-Manager

cat <<EOF > /default-comfyui-bundle/ComfyUI/user/default/ComfyUI-Manager/config.ini
[default]
use_uv = False
EOF

WORKFLOW_DEPS_JSON="${WORKFLOW_DEPS_JSON:-}"
WORKFLOW_REQUIREMENTS_TXT="${WORKFLOW_REQUIREMENTS_TXT:-/builder-scripts/workflow-requirements.txt}"
WORKFLOW_SUMMARY_JSON="${WORKFLOW_SUMMARY_JSON:-/builder-scripts/workflow-summary.json}"

if [ -n "${WORKFLOW_DEPS_JSON}" ] && [ -f "${WORKFLOW_DEPS_JSON}" ]; then
    echo "########################################"
    echo "[INFO] 按工作流解析自定义节点..."
    echo "########################################"
    python3 /builder-scripts/apply_workflow_custom_nodes.py \
        --deps "${WORKFLOW_DEPS_JSON}" \
        --custom-node-root /default-comfyui-bundle/ComfyUI/custom_nodes \
        --requirements-output "${WORKFLOW_REQUIREMENTS_TXT}" \
        --summary-output "${WORKFLOW_SUMMARY_JSON}" \
        --pak3 /builder-scripts/pak3.txt \
        --pak7 /builder-scripts/pak7.txt
else
    echo "[INFO] 未检测到工作流依赖文件，保持默认插件集合。"
    if [ -f "${WORKFLOW_REQUIREMENTS_TXT}" ]; then
        rm -f "${WORKFLOW_REQUIREMENTS_TXT}"
    fi
fi

echo "########################################"
echo "[INFO] Downloading Models..."
echo "########################################"

# VAE Models
cd /default-comfyui-bundle/ComfyUI/models/vae

aria2c 'https://github.com/madebyollin/taesd/raw/refs/heads/main/taesdxl_decoder.pth'
aria2c 'https://github.com/madebyollin/taesd/raw/refs/heads/main/taesd_decoder.pth'
aria2c 'https://github.com/madebyollin/taesd/raw/refs/heads/main/taesd3_decoder.pth'
aria2c 'https://github.com/madebyollin/taesd/raw/refs/heads/main/taef1_decoder.pth'
