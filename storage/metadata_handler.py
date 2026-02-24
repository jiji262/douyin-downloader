import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import aiofiles
from utils.logger import setup_logger

logger = setup_logger("MetadataHandler")


class MetadataHandler:
    def __init__(self):
        self._manifest_lock = asyncio.Lock()

    async def save_metadata(self, data: Dict[str, Any], save_path: Path) -> bool:
        try:
            async with aiofiles.open(save_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            logger.error(f"Failed to save metadata: {save_path}, error: {e}")
            return False

    async def append_download_manifest(
        self, base_path: Path, record: Dict[str, Any]
    ) -> bool:
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
            logger.error(
                f"Failed to append download manifest: {manifest_path}, error: {e}"
            )
            return False

    async def load_metadata(self, file_path: Path) -> Dict[str, Any]:
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to load metadata: {file_path}, error: {e}")
            return {}
