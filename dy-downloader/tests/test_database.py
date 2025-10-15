import json

import pytest

from storage import Database


@pytest.mark.asyncio
async def test_database_aweme_lifecycle(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))

    await database.initialize()

    aweme_payload = {
        'aweme_id': '123',
        'aweme_type': 'video',
        'title': 'test',
        'author_id': 'author',
        'author_name': 'Author',
        'create_time': 1700000000,
        'file_path': '/tmp',
        'metadata': json.dumps({'a': 1}, ensure_ascii=False),
    }

    await database.add_aweme(aweme_payload)

    assert await database.is_downloaded('123') is True
    assert await database.get_aweme_count_by_author('author') == 1
    assert await database.get_latest_aweme_time('author') == 1700000000

    await database.add_history({
        'url': 'https://www.douyin.com/video/123',
        'url_type': 'video',
        'total_count': 1,
        'success_count': 1,
        'config': json.dumps({'path': './Downloaded/'}, ensure_ascii=False),
    })
