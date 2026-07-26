import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import aiofiles

from utils.logger import setup_logger

logger = setup_logger("MetadataHandler")


def _reserve_temp_path(save_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        dir=str(save_path.parent),
        prefix=f".{save_path.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    return Path(name)


class MetadataHandler:
    def __init__(self):
        self._manifest_lock = asyncio.Lock()
        self._metadata_replace_lock = asyncio.Lock()

    async def save_metadata(self, data: Dict[str, Any], save_path: Path) -> bool:
        tmp_path = None
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = await asyncio.to_thread(_reserve_temp_path, save_path)
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                await f.flush()
            async with self._metadata_replace_lock:
                await asyncio.to_thread(os.replace, tmp_path, save_path)
            tmp_path = None
            return True
        except Exception as e:
            logger.error("Failed to save metadata: %s, error: %s", save_path, e)
            return False
        finally:
            if tmp_path is not None:
                try:
                    await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
                except OSError:
                    pass

    async def append_download_manifest(self, base_path: Path, record: Dict[str, Any]) -> bool:
        manifest_path = base_path / "download_manifest.jsonl"
        normalized_record = {
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            **record,
        }

        try:
            async with self._manifest_lock:
                async with aiofiles.open(manifest_path, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(normalized_record, ensure_ascii=False))
                    await f.write("\n")
            return True
        except Exception as e:
            logger.error("Failed to append download manifest: %s, error: %s", manifest_path, e)
            return False

    async def load_metadata(self, file_path: Path) -> Dict[str, Any]:
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error("Failed to load metadata: %s, error: %s", file_path, e)
            return {}
