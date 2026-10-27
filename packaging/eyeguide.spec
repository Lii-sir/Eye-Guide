# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


PROJECT_ROOT = Path.cwd()
APP_ENTRY = PROJECT_ROOT / "app.py"
TORCH_VARIANT = os.environ.get("EYEGUIDE_TORCH_VARIANT", "cpu").lower()


def existing_data_entry(relative_path: str, target_dir: str = "."):
    source = PROJECT_ROOT / relative_path
    if source.exists():
        return (str(source), target_dir)
    return None


datas = []
for package_name in ("ultralytics", "transformers", "huggingface_hub", "tokenizers"):
    datas += collect_data_files(package_name)

for relative_path in ("yolo26n.pt", "yolo11n.pt", "README.md"):
    entry = existing_data_entry(relative_path)
    if entry is not None:
        datas.append(entry)

entry = existing_data_entry("assets/icons", "assets/icons")
if entry is not None:
    datas.append(entry)

depth_model_dir = PROJECT_ROOT / "models" / "depth-anything-v2"
if depth_model_dir.exists():
    datas.append((str(depth_model_dir), "models/depth-anything-v2"))

binaries = []
for package_name in ("torch", "torchvision"):
    binaries += collect_dynamic_libs(package_name)

def filtered_binaries(items):
    if TORCH_VARIANT == "gpu":
        return items

    excluded_prefixes = (
        "torch_cuda",
        "c10_cuda",
        "cublas",
        "cudnn",
        "cufft",
        "curand",
        "cusolver",
        "cusparse",
        "cupti",
        "cudart",
        "nvrtc",
        "nvtoolsext",
        "caffe2_nvrtc",
    )

    filtered = []
    for source, target in items:
        name = Path(source).name.lower()
        if name.startswith(excluded_prefixes):
            continue
        filtered.append((source, target))
    return filtered


binaries = filtered_binaries(binaries)


hiddenimports = [
    "pythoncom",
    "pywintypes",
    "win32timezone",
    "win32com",
    "win32com.client",
    "win32com.client.dynamic",
    "ultralytics",
    "ultralytics.trackers",
    "ultralytics.trackers.byte_tracker",
    "transformers.models.depth_anything",
    "transformers.models.depth_anything.configuration_depth_anything",
    "transformers.models.depth_anything.modeling_depth_anything",
    "transformers.models.dpt.image_processing_dpt",
    "transformers.models.dinov2.configuration_dinov2",
    "transformers.models.dinov2.modeling_dinov2",
]

excludes = [
    "torchaudio",
    "tensorflow",
    "tensorboard",
    "IPython",
    "pytest",
    "win32com.demos",
    "win32com.test",
    "transformers.cli",
    "torch.testing",
]

a = Analysis(
    [str(APP_ENTRY)],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "runtime_hook_dlls.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EyeGuide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EyeGuide",
)
