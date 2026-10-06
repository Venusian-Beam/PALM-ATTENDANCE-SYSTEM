"""Download the face models PAS needs into models/."""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.face.engine import (ANTISPOOF_FILE, LANDMARKER_FILE, OBJECTS_FILE,  # noqa: E402
                                 SFACE_FILE, YUNET_FILE)

MODELS = {
    YUNET_FILE: "https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    SFACE_FILE: "https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    LANDMARKER_FILE: "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                     "face_landmarker/float16/1/face_landmarker.task",
    # Anti-spoofing (photo / screen detection): MiniFASNetV2 from Silent-Face-Anti-Spoofing,
    # Apache-2.0, ONNX export by yakhyo/face-anti-spoofing.
    ANTISPOOF_FILE: "https://github.com/yakhyo/face-anti-spoofing/releases/download/"
                    "weights/MiniFASNetV2.onnx",
    # Object detection (phones, laptops, screens, books): NanoDet-Plus, OpenCV zoo, Apache-2.0.
    OBJECTS_FILE: "https://github.com/opencv/opencv_zoo/raw/main/models/"
                  "object_detection_nanodet/object_detection_nanodet_2022nov.onnx",
}

# Pinned checksums for the security models, so a tampered download is refused.
SHA256 = {
    ANTISPOOF_FILE: "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
    OBJECTS_FILE: "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186",
}

MIN_BYTES = 100_000  # guards against saving an HTML error page instead of a model


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(name: str, url: str, dest: Path) -> None:
    target = dest / name
    if target.exists() and target.stat().st_size >= MIN_BYTES:
        if name in SHA256 and sha256(target) != SHA256[name]:
            print(f"  {name}: checksum mismatch, downloading again")
        else:
            print(f"  {name}: already present")
            return
    print(f"  {name}: downloading…")
    tmp = target.with_suffix(target.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    if tmp.stat().st_size < MIN_BYTES:
        tmp.unlink()
        raise RuntimeError(f"{name}: the download looks wrong (too small). URL: {url}")
    if name in SHA256 and sha256(tmp) != SHA256[name]:
        tmp.unlink()
        raise RuntimeError(f"{name}: checksum mismatch; the file was not saved. URL: {url}")
    tmp.replace(target)
    print(f"  {name}: {target.stat().st_size / 1e6:.1f} MB")


def main() -> None:
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    print(f"Saving models to {dest}")
    for name, url in MODELS.items():
        download(name, url, dest)
    print("Done.")


if __name__ == "__main__":
    main()
