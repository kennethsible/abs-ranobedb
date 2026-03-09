import argparse
import asyncio
import logging
import os
import pprint
import re
import signal
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp_client_cache.backends.sqlite import SQLiteBackend
from aiohttp_client_cache.session import CachedSession
from aiolimiter import AsyncLimiter
from langcodes import Language

from absranobedb import __version__

API_URL = 'https://ranobedb.org/api/v0'
SEARCH_LIMIT = str(os.getenv('SEARCH_LIMIT') or os.getenv('MAX_RESULTS') or '5')
PREFER_ROMAJI = os.getenv('PREFER_ROMAJI', 'true').lower() in ('true', '1', 't')

logger = logging.getLogger('abs-ranobedb')


def extract_title(summary: dict[str, Any], tag: str) -> str:
    if PREFER_ROMAJI and tag == 'ja':
        return summary.get('romaji') or summary.get('romaji_orig') or summary.get('title') or ''
    return summary.get('title') or summary.get('romaji') or summary.get('romaji_orig') or ''


def extract_author(details: dict[str, Any], tag: str) -> str:
    author_names: list[str] = []
    for edition in details.get('editions', []):
        for staff in edition.get('staff', []):
            if staff.get('role_type') in ['author', 'artist']:
                if not PREFER_ROMAJI and tag == 'ja':
                    name = staff.get('name') or staff.get('romaji')
                else:
                    name = staff.get('romaji') or staff.get('name')
                if name and name not in author_names:
                    author_names.append(name)
    if author_names:
        return ', '.join(author_names)
    return ''


def extract_series_name(details: dict[str, Any], tag: str) -> str:
    if series_data := details.get('series', {}):
        if PREFER_ROMAJI and tag == 'ja':
            return (
                series_data.get('romaji')
                or series_data.get('romaji_orig')
                or series_data.get('title')
                or ''
            )
        return (
            series_data.get('title')
            or series_data.get('romaji')
            or series_data.get('romaji_orig')
            or ''
        )
    return ''


def extract_sequence(details: dict[str, Any], book_id: int) -> str:
    series_details = details.get('series', {})
    for index, book_details in enumerate(series_details.get('books', [])):
        if book_details.get('id') == book_id:
            return str(index + 1)
    return ''


def extract_series(details: dict[str, Any], tag: str, book_id: int | None) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    if book_id is not None:
        series_data = details.get('series', {})
        if len(series_data.get('books', [])) > 1:
            if series_name := extract_series_name(details, tag):
                sequence = extract_sequence(details, int(book_id))
                series.append({'series': series_name, 'sequence': sequence})
    return series


def extract_description(details: dict[str, Any], tag: str) -> str:
    if not PREFER_ROMAJI and tag == 'ja':
        description = str(details.get('description_ja', '')).strip()
    else:
        description = str(details.get('description', '')).strip()
    description = re.sub(r'\s*\[From\s+.*\]\s*$', '', description)
    return description.strip()


def extract_genres(details: dict[str, Any]) -> list[str]:
    genres: list[str] = []
    series_data = details.get('series', {})
    for tag in series_data.get('tags', []):
        tag_name = tag.get('name', '')
        if tag_name and tag.get('ttype') == 'genre':
            genres.append(tag_name.title())
    return genres


def extract_publisher(details: dict[str, Any], tag: str) -> str:
    publishers = details.get('publishers', [])
    if sorted_publishers := sorted(publishers, key=lambda x: 0 if x.get('lang') == tag else 1):
        publisher = sorted_publishers[0]
        if PREFER_ROMAJI and tag == 'ja':
            return publisher.get('romaji') or publisher.get('name') or ''
        return publisher.get('name') or publisher.get('romaji') or ''
    return ''


def extract_year(details: dict[str, Any], tag: str, date: int | None = None) -> str:
    release_dates = details.get('c_release_dates', {})
    if release_date := release_dates.get(tag) or details.get('c_release_date') or date:
        return str(release_date)[:4]
    return ''


def extract_language(tag: str) -> str:
    return Language.get(tag).display_name() if tag else ''


def extract_cover(details: dict[str, Any]) -> str:
    image_data = details.get('image', {})
    if image_data and image_data.get('filename'):
        return f'https://images.ranobedb.org/{image_data["filename"]}'
    return ''


def extract_identifiers(details: dict[str, Any], tag: str) -> dict[str, str]:
    identifiers = {'isbn': '', 'asin': ''}
    releases = details.get('releases', [])
    sorted_releases = sorted(releases, key=lambda x: 0 if x.get('lang') == tag else 1)
    for release in sorted_releases:
        isbn, amazon_link = release.get('isbn13'), release.get('amazon')
        if not identifiers['isbn'] and isbn:
            identifiers['isbn'] = str(isbn)
        if not identifiers['asin'] and amazon_link:
            if match := re.search(r'/dp/([A-Z0-9]{10})', str(amazon_link), re.IGNORECASE):
                identifiers['asin'] = match.group(1).upper()
        if identifiers['isbn'] and identifiers['asin']:
            break
    return identifiers


async def fetch_book_data(
    book_id: int, session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> dict[str, Any]:
    try:
        async with limiter:
            async with session.get(f'{API_URL}/book/{book_id}') as response:
                response.raise_for_status()
                data = await response.json()
                if isinstance(data, dict):
                    summary = data.get('book', {})
                    if isinstance(summary, dict):
                        return summary
                return {}
    except aiohttp.ClientError as e:
        logger.warning(f'failed to fetch details for book {book_id}: {e}')
        return {}


async def fetch_staff_ids(
    author: str, session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> list[str]:
    try:
        async with limiter:
            async with session.get(f'{API_URL}/staff', params={'q': author}) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    return []
                staff_ids: list[str] = []
                for person in data.get('staff', []):
                    if not isinstance(person, dict):
                        continue
                    if staff_id := person.get('id'):
                        staff_ids.append(str(staff_id))
                return staff_ids
    except aiohttp.ClientError as e:
        logger.warning(f'failed to fetch IDs for author {author}: {e}')
        return []


async def gather_authors(
    author_string: str, session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> list[list[str]]:
    authors = [a.strip() for a in re.split(r'[;,|]', author_string) if a.strip()]
    tasks = [fetch_staff_ids(author, session, limiter) for author in authors]
    return await asyncio.gather(*tasks)


async def extract_metadata(
    summary: dict[str, Any], session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> tuple[Any | None, dict[str, Any]]:
    details: dict[str, Any] = {}
    if book_id := summary.get('id'):
        details = await fetch_book_data(int(book_id), session, limiter)
    tag = details.get('lang', summary.get('lang', ''))
    date = summary.get('c_release_date')
    identifiers = extract_identifiers(details, tag)
    return (
        book_id,
        {
            'title': extract_title(summary, tag),
            'subtitle': details.get('subtitle', ''),
            'author': extract_author(details, tag),
            'series': extract_series(details, tag, book_id),
            'description': extract_description(details, tag),
            'genres': extract_genres(details),
            'publisher': extract_publisher(details, tag),
            'publishedYear': extract_year(details, tag, date),
            'language': extract_language(tag),
            'cover': extract_cover(details),
            'isbn': identifiers['isbn'],
            'asin': identifiers['asin'],
        },
    )


async def gather_matches(
    data: dict[str, Any], session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> list[tuple[Any | None, dict[str, Any]]]:
    tasks = [extract_metadata(summary, session, limiter) for summary in data.get('books', [])]
    return await asyncio.gather(*tasks)


async def search(request: web.Request) -> web.Response:
    if not (query := request.query.get('query')):
        logger.warning('received search request with empty query')
        return web.json_response({'error': 'empty query'}, status=400)

    session = request.app['client_session']
    limiter = request.app['limiter']

    try:
        params = [('q', query), ('limit', SEARCH_LIMIT)]
        if author_string := request.query.get('author'):
            author_matches = await gather_authors(author_string, session, limiter)
            staff_ids = list(dict.fromkeys(staff_id for ids in author_matches for staff_id in ids))
            if staff_ids:
                for staff_id in staff_ids:
                    params.append(('staff', staff_id))
                if len(staff_ids) > 1:
                    params.append(('sl', 'or'))
                suffix = '' if len(staff_ids) == 1 else 'es'
                logger.info(f"found {len(staff_ids)} author match{suffix} for '{author_string}'")
                for i, staff_id in enumerate(staff_ids, start=1):
                    logger.debug(f'({i}) https://ranobedb.org/staff/{staff_id}')

        async with limiter:
            async with session.get(f'{API_URL}/books', params=params) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    logger.error(f"upstream returned invalid data type for '{query}'")
                    return web.json_response({'error': 'invalid upstream response'}, status=502)
                query_matches = await gather_matches(data, session, limiter)
                suffix = '' if len(query_matches) == 1 else 'es'
                logger.info(f"found {len(query_matches)} query match{suffix} for '{query}'")
                for i, (book_id, match) in enumerate(query_matches, start=1):
                    logger.debug(
                        f'({i}) https://ranobedb.org/book/{book_id}\n' + pprint.pformat(match)
                    )
                return web.json_response({'matches': [match for _, match in query_matches]})

    except aiohttp.ClientResponseError as e:
        logger.error(f"upstream API error for '{query}': {e.status}")
        return web.json_response({'error': f'upstream API error: {e}'}, status=502)
    except aiohttp.ClientError as e:
        logger.error(f"upstream connection failed for '{query}': {e}")
        return web.json_response({'error': f'upstream connection failed: {e}'}, status=502)
    except asyncio.TimeoutError:
        logger.error(f"upstream API timed out for '{query}'")
        return web.json_response({'error': 'upstream API timed out'}, status=504)
    except ValueError as e:
        logger.error(f"invalid upstream response for '{query}': {e}")
        return web.json_response({'error': f'invalid upstream response: {e}'}, status=502)
    except Exception as e:
        logger.exception(f"internal server error while processing '{query}'")
        return web.json_response({'error': f'internal server error: {e}'}, status=500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--cache-dir', type=str)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='[%(asctime)s %(levelname)s] [%(name)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

    async def on_startup(app: web.Application) -> None:
        if args.cache_dir:
            cache_dir = Path(args.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = str(cache_dir / 'ranobedb')
            cache = SQLiteBackend(
                cache_name=cache_file,
                expire_after=60 * 60 * 24 * 7,
                allowed_methods=('GET',),
                allowed_codes=(200,),
            )
            app['client_session'] = CachedSession(cache=cache)
        else:
            app['client_session'] = aiohttp.ClientSession()
        app['limiter'] = AsyncLimiter(60, 60)

    async def on_cleanup(app: web.Application) -> None:
        await app['client_session'].close()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get('/search', search)

    async def run_server() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
        loop.add_signal_handler(signal.SIGINT, stop_event.set)

        await stop_event.wait()
        await runner.cleanup()

    logger.info(f'ABS-RanobeDB Metadata Provider {__version__}')
    logger.info(f'running server on {args.host}:{args.port}')
    asyncio.run(run_server())


if __name__ == '__main__':
    main()
