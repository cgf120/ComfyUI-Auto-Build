################################################################################
# Dockerfile that builds 'yanwk/comfyui-boot:cu128-slim'
# A runtime environment for https://github.com/comfyanonymous/ComfyUI
# Using CUDA 12.8, Python 3.12
# The container will be running in root (easy for rootless deploy).
################################################################################

FROM docker.io/opensuse/tumbleweed:latest

LABEL maintainer="YAN Wenkun <code@yanwk.fun>"

RUN set -eu

################################################################################
# Python and tools
# Since this image is so big, we use openSUSE-verified PIP packages for compatibility.

RUN --mount=type=cache,target=/var/cache/zypp \
    zypper addrepo --check --refresh --priority 90 \
        'https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/' packman-essentials \
    && zypper --gpg-auto-import-keys \
        install --no-confirm --auto-agree-with-licenses \
python312-devel \
python312-pip \
python312-wheel \
python312-setuptools \
python312-Cython \
python312-py-build-cmake \
python312-matplotlib \
python312-mpmath \
# python312-numpy \ # Numba conflicts with latest NumPy
python312-onnx \
python312-pandas \
python312-scikit-build \
python312-scikit-build-core-pyproject \
python312-scikit-image \
python312-scikit-learn \
python312-scipy

RUN --mount=type=cache,target=/var/cache/zypp \
    zypper --gpg-auto-import-keys \
        install --no-confirm --auto-agree-with-licenses \
python312-opencv \
opencv \
opencv-devel \
Mesa-libGL1 \
Mesa-libEGL-devel \
libgthread-2_0-0 \
libQt5OpenGL5

RUN --mount=type=cache,target=/var/cache/zypp \
    zypper --gpg-auto-import-keys \
        install --no-confirm --auto-agree-with-licenses \
python312-ffmpeg-python \
python312-imageio \
python312-svglib \
ffmpeg \
x264 \
x265 \
python312-GitPython \
python312-pygit2 \
git

RUN --mount=type=cache,target=/var/cache/zypp \
    zypper --gpg-auto-import-keys \
        install --no-confirm --auto-agree-with-licenses \
make \
ninja \
aria2 \
findutils \
fish \
fd \
fuse \
vim \
which \
google-noto-sans-fonts \
    && rm -v /usr/lib64/python3.12/EXTERNALLY-MANAGED \
    # Ensure default Python version
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 100

# Temp fix for OpenCV on openSUSE
ENV LD_PRELOAD=/usr/lib64/libjpeg.so.8

# Temp fix for SentencePiece on CMAKE 4+
ENV CMAKE_POLICY_VERSION_MINIMUM=3.5

################################################################################
# GCC 14 
# Compatible with CUDA 12.8.
# https://docs.nvidia.com/cuda/archive/12.8.1/cuda-installation-guide-linux/index.html#host-compiler-support-policy

RUN --mount=type=cache,target=/var/cache/zypp \
    zypper --gpg-auto-import-keys \
        install --no-confirm --auto-agree-with-licenses \
gcc14 \
gcc14-c++ \
cpp14 \
    && update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-14 90 \
    && update-alternatives --install /usr/bin/cc  cc  /usr/bin/gcc-14 90 \
    && update-alternatives --install /usr/bin/cpp cpp /usr/bin/cpp-14 90 \
    && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 90 \
    && update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 90 \
    && update-alternatives --install /usr/bin/gcc-ar gcc-ar /usr/bin/gcc-ar-14 90 \
    && update-alternatives --install /usr/bin/gcc-nm gcc-nm /usr/bin/gcc-nm-14 90 \
    && update-alternatives --install /usr/bin/gcc-ranlib gcc-ranlib /usr/bin/gcc-ranlib-14 90 \
    && update-alternatives --install /usr/bin/gcov gcov /usr/bin/gcov-14 90 \
    && update-alternatives --install /usr/bin/gcov-dump gcov-dump /usr/bin/gcov-dump-14 90 \
    && update-alternatives --install /usr/bin/gcov-tool gcov-tool /usr/bin/gcov-tool-14 90 

################################################################################
# Python Packages

# PyTorch, xFormers
# Break down the steps, so we have more but smaller image layers.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip list \
    && pip install \
        --upgrade pip wheel setuptools

# 1. Dry run pip install to get the list of packages
#    (first run to show logs, then grep the output)
# 2. Extract the package names and versions from the output
#    Example: nvidia-cuda-runtime-cu12-12.6.77 -> nvidia-cuda-runtime-cu12==12.6.77
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        --dry-run xformers torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128 \
    && pip install \
        --dry-run xformers torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128 \
            | grep -oP '(?<=Would install ).*' > /tmp/packages.txt \
    && grep -oP 'nvidia-\S+-\S+' /tmp/packages.txt \
        | sed 's/\(.*\)-\([^-]*\)$/\1==\2/' > /tmp/packages-nvidia.txt \
    && echo "========== NVIDIA Packages ==========" \
    && cat /tmp/packages-nvidia.txt \
    && echo "====================================="

# Install big 'nvidia-' packages first
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps \
        "$(grep '^nvidia-cublas' /tmp/packages-nvidia.txt)" \
        --index-url https://download.pytorch.org/whl/cu128

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps \
        "$(grep '^nvidia-cudnn' /tmp/packages-nvidia.txt)" \
        --index-url https://download.pytorch.org/whl/cu128

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps \
        "$(grep '^nvidia-cusparse-' /tmp/packages-nvidia.txt)" \
        --index-url https://download.pytorch.org/whl/cu128

# Install all 'nvidia-' packages, 5 at a time
RUN --mount=type=cache,target=/root/.cache/pip \
    head -n 5 /tmp/packages-nvidia.txt | while read -r pkg; do \
        pip install --no-deps "$pkg" \
            --index-url https://download.pytorch.org/whl/cu128; \
    done

RUN --mount=type=cache,target=/root/.cache/pip \
    sed -n '6,10p' /tmp/packages-nvidia.txt | while read -r pkg; do \
        pip install --no-deps "$pkg" \
            --index-url https://download.pytorch.org/whl/cu128; \
    done

RUN --mount=type=cache,target=/root/.cache/pip \
    tail -n +11 /tmp/packages-nvidia.txt | while read -r pkg; do \
        pip install --no-deps "$pkg" \
            --index-url https://download.pytorch.org/whl/cu128; \
    done

# Install PyTorch alone
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps \
        "$(grep -oP 'torch-\S+' /tmp/packages.txt | sed 's/\(.*\)-\([^-]*\)$/\1==\2/')" \
        --index-url https://download.pytorch.org/whl/cu128 \
    && rm -v /tmp/packages.txt /tmp/packages-nvidia.txt

# Install everything else, also works as a fail-safe
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        xformers torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128

# Install Triton (redundant)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        triton pytorch-triton \
        --index-url https://download.pytorch.org/whl/cu128

# Bind libs (.so files)
# Ref: https://github.com/pytorch/pytorch/blob/main/.ci/manywheel/build_cuda.sh
ENV LD_LIBRARY_PATH="/usr/local/lib64/python3.12/site-packages/torch/lib\
:/usr/local/lib/python3.12/site-packages/cusparselt/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cuda_cupti/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cufft/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cufile/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/curand/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cusolver/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cusparse/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/cusparselt/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/nccl/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/nvjitlink/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/nvshmem/lib\
:/usr/local/lib/python3.12/site-packages/nvidia/nvtx/lib"

# Deps for ComfyUI & custom nodes
COPY builder-scripts/.  /builder-scripts/

ARG WORKFLOW_JSON=""
ENV WORKFLOW_JSON_SOURCE="${WORKFLOW_JSON}"
ENV WORKFLOW_DEPS_JSON="/tmp/workflow-deps.json"
ENV WORKFLOW_REQUIREMENTS_TXT="/builder-scripts/workflow-requirements.txt"
ENV WORKFLOW_SUMMARY_JSON="/builder-scripts/workflow-summary.json"

RUN if [ -n "${WORKFLOW_JSON_SOURCE}" ]; then \
        case "${WORKFLOW_JSON_SOURCE}" in \
            /*) \
                echo "[error] WORKFLOW_JSON 必须指向构建上下文内的相对路径，例如 builder-scripts/workflows/foo.json"; \
                exit 1; \
                ;; \
        esac; \
        CLEAN_SOURCE="${WORKFLOW_JSON_SOURCE#./}"; \
        BUILDER_REL="${CLEAN_SOURCE#builder-scripts/}"; \
        if [ -f "${CLEAN_SOURCE}" ]; then \
            WORKFLOW_PATH="${CLEAN_SOURCE}"; \
        elif [ -f "/builder-scripts/${BUILDER_REL}" ]; then \
            WORKFLOW_PATH="/builder-scripts/${BUILDER_REL}"; \
        elif [ -f "/${WORKFLOW_JSON_SOURCE}" ]; then \
            WORKFLOW_PATH="/${WORKFLOW_JSON_SOURCE}"; \
        else \
            echo "[error] 未找到工作流文件: ${WORKFLOW_JSON_SOURCE}" >&2; \
            exit 1; \
        fi; \
        cp "${WORKFLOW_PATH}" /tmp/workflow.json; \
        python3 /builder-scripts/generate_workflow_dependencies.py \
            --workflow /tmp/workflow.json \
            --special-config /builder-scripts/special-node-overrides.json \
            --output "${WORKFLOW_DEPS_JSON}"; \
    else \
        echo "[INFO] 未提供 WORKFLOW_JSON，跳过工作流依赖解析。"; \
    fi

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        -r /builder-scripts/pak3.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        -r /builder-scripts/pak7.txt

################################################################################
# Pre-download a ComfyUI bundle in the image

WORKDIR /default-comfyui-bundle

RUN bash /builder-scripts/preload-cache.sh

# Install deps (comfyui-frontend-package, etc) pair to the preloaded ComfyUI version
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        -r '/default-comfyui-bundle/ComfyUI/requirements.txt' \
        -r '/default-comfyui-bundle/ComfyUI/custom_nodes/ComfyUI-Manager/requirements.txt' \
    && pip list

RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -f /builder-scripts/workflow-requirements.txt ]; then \
        pip install -r /builder-scripts/workflow-requirements.txt; \
    else \
        echo "[INFO] 未检测到 workflow-requirements.txt，跳过按需依赖。"; \
    fi

################################################################################

RUN du -ah /root \
    && rm -rfv /root/* \
    && rm -rfv /root/.[^.]* /root/.??*

COPY runner-scripts/.  /runner-scripts/

USER root
VOLUME /root
WORKDIR /root
EXPOSE 8188
ENV CLI_ARGS=""
CMD ["bash","/runner-scripts/entrypoint.sh"]
