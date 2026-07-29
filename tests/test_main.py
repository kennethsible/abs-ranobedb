import json
from typing import Any, AsyncIterator, cast

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from aiolimiter import AsyncLimiter

from absranobedb.main import (
    extract_author,
    extract_cover,
    extract_description,
    extract_genres,
    extract_identifiers,
    extract_language,
    extract_publisher,
    extract_sequence,
    extract_series,
    extract_series_name,
    extract_tags,
    extract_title,
    extract_year,
    scrape_cover,
    search,
)

asyncio_test = pytest.mark.asyncio(loop_scope='function')

SESSION_KEY = web.AppKey('client_session', aiohttp.ClientSession)
LIMITER_KEY = web.AppKey('limiter', AsyncLimiter)


@pytest_asyncio.fixture(loop_scope='function')
async def mock_app() -> AsyncIterator[web.Application]:
    async with aiohttp.ClientSession() as session:
        app = web.Application()
        app[SESSION_KEY] = session
        app[LIMITER_KEY] = AsyncLimiter(60, 60)
        app._state['client_session'] = session
        app._state['limiter'] = AsyncLimiter(60, 60)
        yield app


@asyncio_test
async def test_search_endpoint(mock_app: web.Application) -> None:
    target_path = '/search?query=KIZUMONOGATARI&author=Nisio+Isin'
    request = make_mocked_request('GET', target_path, app=mock_app)
    response = await search(request)
    assert response.status == 200

    assert response.body is not None
    data = json.loads(response.body.decode('utf-8'))
    assert 'matches' in data and len(data['matches']) > 0

    target_match = None
    for match in data['matches']:
        if match.get('isbn') == '9781941220979':
            target_match = match
            break
    assert target_match is not None

    expected_keys = [
        'title',
        'subtitle',
        'author',
        'series',
        'description',
        'genres',
        'tags',
        'publisher',
        'publishedYear',
        'language',
        'cover',
        'isbn',
        'asin',
    ]
    for key in expected_keys:
        assert key in target_match

    assert isinstance(target_match['title'], str) and len(target_match['title']) > 0
    assert isinstance(target_match['subtitle'], str)
    assert isinstance(target_match['author'], str)
    assert isinstance(target_match['series'], list)
    assert isinstance(target_match['description'], str)
    assert isinstance(target_match['genres'], list)
    assert isinstance(target_match['tags'], list)
    assert isinstance(target_match['publisher'], str)
    assert isinstance(target_match['publishedYear'], str)
    assert isinstance(target_match['language'], str)
    assert isinstance(target_match['cover'], str)
    assert isinstance(target_match['isbn'], str)
    assert isinstance(target_match['asin'], str)


@asyncio_test
async def test_scrape_cover(mock_app: web.Application) -> None:
    session = cast(aiohttp.ClientSession, mock_app._state['client_session'])
    limiter = cast(AsyncLimiter, mock_app._state['limiter'])

    cover_url = await scrape_cover('1941220975', session, limiter)
    assert 'media-amazon.com/images/I/' in cover_url or '1941220975' in cover_url

    cover_url = await scrape_cover('194122097X', session, limiter)
    assert 'images-na.ssl-images-amazon.com/images/P/' in cover_url

    cover_url = await scrape_cover('', session, limiter)
    assert cover_url == ''


def test_extract_title(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {'title': 'KIZUMONOGATARI', 'romaji': None, 'romaji_orig': 'Kizumonogatari'}

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    title = extract_title(summary, tag='ja')
    assert title == 'Kizumonogatari'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', False)
    title = extract_title(summary, tag='ja')
    assert title == 'KIZUMONOGATARI'

    del summary['romaji_orig']
    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    title = extract_title(summary, tag='ja')
    assert title == 'KIZUMONOGATARI'


def test_extract_author(monkeypatch: pytest.MonkeyPatch) -> None:
    details = {
        'editions': [
            {
                'staff': [
                    {'role_type': 'author', 'name': '西尾維新', 'romaji': 'Nisio Isin'},
                    {'role_type': 'artist', 'name': 'VOFAN', 'romaji': None},
                ]
            }
        ]
    }

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    authors = extract_author(details, tag='ja')
    assert authors == 'Nisio Isin, VOFAN'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', False)
    authors = extract_author(details, tag='ja')
    assert authors == '西尾維新, VOFAN'

    assert extract_author({}, tag='en') == ''


def test_extract_series_name(monkeypatch: pytest.MonkeyPatch) -> None:
    details = {
        'series': {'title': 'MONOGATARI', 'romaji': None, 'romaji_orig': 'Monogatari Series'}
    }

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    assert extract_series_name(details, tag='ja') == 'Monogatari Series'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', False)
    assert extract_series_name(details, tag='ja') == 'MONOGATARI'

    assert extract_series_name({}, tag='en') == ''


def test_extract_sequence() -> None:
    details = {'series': {'books': [{'id': 17860}, {'id': 17874}, {'id': 17873}, {'id': 4303}]}}
    assert extract_sequence(details, book_id=4303) == '4'
    assert extract_sequence(details, book_id=99) == ''


def test_extract_series() -> None:
    details = {
        'series': {
            'title': 'MONOGATARI',
            'books': [{'id': 17860}, {'id': 17874}, {'id': 17873}, {'id': 4303}],
        }
    }
    series_list = extract_series(details, tag='en', book_id=4303)
    assert series_list == [{'series': 'MONOGATARI', 'sequence': '4'}]
    assert extract_series(details, tag='en', book_id=None) == []

    single_book_details = {'series': {'title': '', 'books': [{'id': 10}]}}
    assert extract_series(single_book_details, tag='en', book_id=10) == []


def test_extract_description(monkeypatch: pytest.MonkeyPatch) -> None:
    details = {
        'description': 'English Text\n\n[From Kodansha]',
        'description_ja': '日本語のテキスト\n\n[From BookWalker]',
    }

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    assert extract_description(details, tag='ja') == 'English Text'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', False)
    assert extract_description(details, tag='ja') == '日本語のテキスト'


def test_extract_genres() -> None:
    details = {'series': {'tags': [{'name': 'young adult fiction', 'ttype': 'genre'}]}}
    assert extract_genres(details) == ['Young Adult Fiction']
    assert extract_genres({}) == []


def test_extract_tags() -> None:
    details = {
        'series': {
            'tags': [
                {'name': 'shounen', 'ttype': 'demographic'},
                {'name': 'anime tie-in', 'ttype': 'tag'},
                {'name': 'manga tie-in', 'ttype': 'tag'},
                {'name': 'school', 'ttype': 'tag'},
                {'name': 'vampire', 'ttype': 'tag'},
            ]
        }
    }
    assert extract_tags(details) == ['Shounen', 'Anime Tie-In', 'Manga Tie-In', 'School', 'Vampire']
    assert extract_tags({}) == []


def test_extract_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    details = {
        'publishers': [
            {'name': '講談社', 'romaji': 'Kodansha', 'lang': 'ja'},
            {'name': 'Vertical', 'romaji': None, 'lang': 'en'},
        ]
    }

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    assert extract_publisher(details, tag='ja') == 'Kodansha'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', False)
    assert extract_publisher(details, tag='ja') == '講談社'

    monkeypatch.setattr('absranobedb.main.PREFER_ROMAJI', True)
    assert extract_publisher(details, tag='en') == 'Vertical'

    assert extract_publisher({}, tag='en') == ''


def test_extract_year() -> None:
    details = {'c_release_dates': {'en': 20151215, 'ja': 20160708}, 'c_release_date': 20151215}
    assert extract_year(details, tag='en') == '2015'
    assert extract_year(details, tag='ja') == '2016'
    assert extract_year({'c_release_dates': {}}, tag='en', date=20170810) == '2017'
    assert extract_year({}, tag='en') == ''


def test_extract_language() -> None:
    assert extract_language('en') == 'English'
    assert extract_language('ja') == 'Japanese'
    assert extract_language('') == ''


def test_extract_cover() -> None:
    details = {'image': {'filename': '8ao8t9A3aiJ3Xmtd.jpg'}}
    assert extract_cover(details) == 'https://images.ranobedb.org/8ao8t9A3aiJ3Xmtd.jpg'
    assert extract_cover({}) == ''


def test_extract_identifiers() -> None:
    details = {
        'releases': [
            {
                'lang': 'en',
                'isbn13': '9781941220979',
                'amazon': 'https://www.amazon.com/dp/1941220975',
            }
        ]
    }

    identifiers = extract_identifiers(details, tag='en')
    assert identifiers['isbn'] == '9781941220979'
    assert identifiers['asin'] == '1941220975'

    no_amazon_details: dict[str, Any] = {'releases': [{'lang': 'en', 'isbn13': '9781941220979'}]}
    no_amazon_identifiers = extract_identifiers(no_amazon_details, tag='en')
    assert no_amazon_identifiers['isbn'] == '9781941220979'
    assert no_amazon_identifiers['asin'] == ''
