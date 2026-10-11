"""Line-delimited JSON adapter around DeepFace for Vela's local worker process.

Input:  {"op":"represent", "path":"C:/photos/example.heic"}
Output: {"ok":true, "faces":[{"region":..., "embedding":[...]}]}

It intentionally exposes face embeddings only. Product-level person names and
clusters stay in Vela's local database; no demographics are requested or stored.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def represent(path: str) -> dict[str, object]:
    from deepface import DeepFace  # Imported lazily: cataloging works without the vision extra installed.

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)
    representations = DeepFace.represent(
        img_path=str(source), model_name="Facenet512", detector_backend="retinaface", align=True, enforce_detection=False
    )
    faces = [{"region": item.get("facial_area", {}), "embedding": item["embedding"]} for item in representations]
    return {"ok": True, "faces": faces}


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("op") != "represent":
                raise ValueError("Only the local represent operation is supported")
            response = represent(request["path"])
        except Exception as error:  # Worker errors are returned to the app instead of killing its queue.
            response = {"ok": False, "error": str(error)}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
