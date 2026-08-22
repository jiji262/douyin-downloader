import asyncio
import json

import pytest

import storage.metadata_handler as metadata_module
from storage.metadata_handler import MetadataHandler


@pytest.mark.asyncio
async def test_save_metadata_concurrently_uses_independent_temp_files(
    tmp_path, monkeypatch
):
    real_open = metadata_module.aiofiles.open
    both_flushed = asyncio.Event()
    flush_count = 0
    flush_lock = asyncio.Lock()

    class _GatedFile:
        def __init__(self, context):
            self._context = context
            self._file = None

        async def __aenter__(self):
            self._file = await self._context.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._context.__aexit__(*args)

        async def write(self, content):
            return await self._file.write(content)

        async def flush(self):
            nonlocal flush_count
            await self._file.flush()
            async with flush_lock:
                flush_count += 1
                if flush_count == 2:
                    both_flushed.set()
            await both_flushed.wait()

    def _gated_open(*args, **kwargs):
        return _GatedFile(real_open(*args, **kwargs))

    monkeypatch.setattr(metadata_module.aiofiles, "open", _gated_open)
    handler = MetadataHandler()
    target = tmp_path / "shared.json"
    payloads = [{"writer": "first"}, {"writer": "second"}]

    results = await asyncio.gather(
        *(handler.save_metadata(payload, target) for payload in payloads)
    )

    assert results == [True, True]
    assert json.loads(target.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.asyncio
async def test_save_metadata_removes_unique_temp_file_after_replace_failure(
    tmp_path, monkeypatch
):
    def _fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(metadata_module.os, "replace", _fail_replace)
    target = tmp_path / "metadata.json"

    saved = await MetadataHandler().save_metadata({"value": 1}, target)

    assert saved is False
    assert list(tmp_path.iterdir()) == []
