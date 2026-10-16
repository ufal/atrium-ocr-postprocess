#!/bin/bash

# This script lives in setup/ but operates on the repository root, so all
# paths below stay root-relative regardless of the caller's working directory.
cd "$(dirname "$0")/.." || exit 1

# Configuration
VENV_DIR="venv-ocr"
SERV_REQUIREMENTS="service/requirements.txt"
MODEL_DIR="models"
FASTTEXT_BIN="lid.176.bin"
# The FastText weights are fetched at the Hub revision the Dockerfile pins and checked against its
# SHA-256, so a local install and the images run the same file. The values are read from the
# Dockerfile's FASTTEXT_REVISION / FASTTEXT_SHA256 build args: one place to bump them.
FASTTEXT_REVISION=$(sed -n 's/^ARG FASTTEXT_REVISION=//p' Dockerfile)
FASTTEXT_SHA256=$(sed -n 's/^ARG FASTTEXT_SHA256=//p' Dockerfile)
if [ -z "$FASTTEXT_REVISION" ] || [ -z "$FASTTEXT_SHA256" ]; then
    echo "❌ Could not read FASTTEXT_REVISION / FASTTEXT_SHA256 from the Dockerfile."
    exit 1
fi
FASTTEXT_URL="https://huggingface.co/facebook/fasttext-language-identification/resolve/${FASTTEXT_REVISION}/model.bin"
LAYOUTREADER_REPO="https://github.com/FreeOCR-AI/layoutreader.git"

echo "🚀 Starting ATRIUM Text Processor API Service Setup..."

# 1. Environment Setup
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment ($VENV_DIR)..."
    python3 -m venv $VENV_DIR
else
    echo "✅ Virtual environment found."
fi

# Activate environment
source $VENV_DIR/bin/activate

# 2. Install Dependencies
if [ -f "$SERV_REQUIREMENTS" ]; then
    echo "⬇️ Installing server dependencies from $SERV_REQUIREMENTS..."
    pip install -r $SERV_REQUIREMENTS
else
    echo "⚠️ Error: $SERV_REQUIREMENTS not found."
    exit 1
fi

# 3. Download LayoutReader 'v3' Scripts
# We use sparse-checkout to download ONLY the 'v3' folder from the repo
if [ ! -d "v3" ]; then
    echo "⬇️ 'v3' directory not found. Fetching from $LAYOUTREADER_REPO..."

    # 1. Clone only the .git info (no files yet) into a temp folder
    TEMP_DIR="_temp_layoutreader"
    git clone --filter=blob:none --no-checkout --depth 1 $LAYOUTREADER_REPO $TEMP_DIR

    pushd $TEMP_DIR > /dev/null || exit 1

    # 2. Initialize sparse checkout to only look for the 'v3' directory
    git sparse-checkout init --cone
    git sparse-checkout set v3

    # 3. Checkout the files (this will only pull the 'v3' folder)
    git checkout

    # 4. Move the folder to the project root
    if [ -d "v3" ]; then
        mv v3 ../
        echo "✅ Successfully fetched 'v3' directory."
    else
        echo "❌ Error: 'v3' directory was not found in the remote repository."
    fi

    popd > /dev/null || exit 1

    # 5. Cleanup temp folder
    rm -rf $TEMP_DIR
else
    echo "✅ 'v3' directory already exists. Skipping download."
fi

# 4. Model Weights Download
echo "🧠 Checking model weights..."

# Ensure model directory exists
mkdir -p $MODEL_DIR

# Download FastText binary if it does not exist
if [ ! -f "$MODEL_DIR/$FASTTEXT_BIN" ]; then
    echo "⬇️ FastText binary not found. Downloading from HuggingFace..."

    # This used to run the identical wget twice — once unconditionally, then
    # again as the success test — fetching ~2 GB two times per invocation.
    if wget "$FASTTEXT_URL" -O "$MODEL_DIR/$FASTTEXT_BIN" && [ -s "$MODEL_DIR/$FASTTEXT_BIN" ] \
        && echo "${FASTTEXT_SHA256}  $MODEL_DIR/$FASTTEXT_BIN" | sha256sum -c - > /dev/null; then
        echo "✅ Successfully downloaded $FASTTEXT_BIN (SHA-256 verified)."
    else
        echo "❌ Failed to download $FASTTEXT_BIN, or its SHA-256 differs from the Dockerfile's pin."
        rm -f "$MODEL_DIR/$FASTTEXT_BIN"
        exit 1
    fi
else
    echo "✅ FastText binary ($FASTTEXT_BIN) already exists. Skipping."
fi

# Note on other models
echo "ℹ️ Note: LayoutLMv3 and the perplexity model (Qwen2.5-0.5B by default) will be automatically downloaded and cached by Hugging Face on the first run."

echo "🎉 Setup complete! To start the server, run:"
echo "   source $VENV_DIR/bin/activate"
echo "   python service/text_api.py"
