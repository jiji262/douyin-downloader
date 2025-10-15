import json
import aiofiles
from pathlib import Path
from typing import Dict, Any
from utils.logger import setup_logger

logger = setup_logger('MetadataHandler')


class MetadataHandler:
    @staticmethod
    async def save_metadata(data: Dict[str, Any], save_path: Path):
        try:
            async with aiofiles.open(save_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"Failed to save metadata: {save_path}, error: {e}")

    @staticmethod
    async def load_metadata(file_path: Path) -> Dict[str, Any]:
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to load metadata: {file_path}, error: {e}")
            return {}
