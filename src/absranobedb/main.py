import argparse
import asyncio
import logging
import os
import pprint
import re
import signal
import sys
from typing import Any

import aiohttp
from aiohttp import web
from aiolimiter import AsyncLimiter
from langcodes import Language

from absranobedb import __version__

API_URL = 'https://ranobedb.org/api/v0'
MAX_RESULTS = str(os.getenv('MAX_RESULTS', '5'))

logger = logging.getLogger('abs-ranobedb')


def extract_title(summary: dict[str, Any]) -> str:
    return summary.get('title') or summary.get('romaji') or summary.get('romaji_orig') or ''


def extract_author(details: dict[str, Any]) -> str:
    author_names: list[str] = []
    for edition in details.get('editions', []):
        for staff in edition.get('staff', []):
            if staff.get('role_type') in ['author', 'artist']:
                name = staff.get('romaji') or staff.get('name')
                if name and name not in author_names:
                    author_names.append(name)
    if author_names:
        return ', '.join(author_names)
    return ''


def extract_series_name(details: dict[str, Any]) -> str:
    if series_data := details.get('series', {}):
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


def extract_series(details: dict[str, Any], book_id: int | None) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    if book_id is not None:
        series_data = details.get('series', {})
        if len(series_data.get('books', [])) > 1:
            if series_name := extract_series_name(details):
                sequence = extract_sequence(details, int(book_id))
                series.append({'series': series_name, 'sequence': sequence})
    return series


def extract_description(details: dict[str, Any]) -> str:
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
        return str(sorted_publishers[0].get('name', ''))
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


def extract_isbn(details: dict[str, Any], tag: str) -> str:
    releases = details.get('releases', [])
    sorted_releases = sorted(releases, key=lambda x: 0 if x.get('lang') == tag else 1)
    for release in sorted_releases:
        isbn = release.get('isbn13')
        if isbn is not None:
            return str(isbn)
    return ''


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


async def extract_metadata(
    summary: dict[str, Any], session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> tuple[Any | None, dict[str, Any]]:
    details: dict[str, Any] = {}
    if book_id := summary.get('id'):
        details = await fetch_book_data(int(book_id), session, limiter)
    tag = details.get('lang', summary.get('lang', ''))
    return (
        book_id,
        {
            'title': extract_title(summary),
            'subtitle': details.get('subtitle', ''),
            'author': extract_author(details),
            'series': extract_series(details, book_id),
            'description': extract_description(details),
            'genres': extract_genres(details),
            'publisher': extract_publisher(details, tag),
            'publishedYear': extract_year(details, tag, summary.get('c_release_date')),
            'language': extract_language(tag),
            'cover': extract_cover(details),
            'isbn': extract_isbn(details, tag),
        },
    )


async def gather_matches(
    data: dict[str, Any], session: aiohttp.ClientSession, limiter: AsyncLimiter
) -> list[tuple[Any | None, dict[str, Any]]]:
    tasks = [extract_metadata(summary, session, limiter) for summary in data.get('books', [])]
    return await asyncio.gather(*tasks)


async def search(request: web.Request) -> web.Response:
    query = request.query.get('query')
    if not query:
        logger.warning('received search request with empty query')
        return web.json_response({'error': 'empty query'}, status=400)

    logger.info(f"searching RanobeDB for '{query}'")
    session = request.app['client_session']
    limiter = request.app['limiter']

    try:
        params = {'q': query, 'limit': MAX_RESULTS}
        async with limiter:
            async with session.get(f'{API_URL}/books', params=params) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    logger.error(f"upstream returned invalid data type for '{query}'")
                    return web.json_response({'error': 'invalid upstream response'}, status=502)
                matches = await gather_matches(data, session, limiter)
                suffix = '' if len(matches) == 1 else 'es'
                logger.info(f"found {len(matches)} match{suffix} for '{query}'")
                for i, (book_id, match) in enumerate(matches, start=1):
                    logger.debug(
                        f'({i}) https://ranobedb.org/book/{book_id}\n' + pprint.pformat(match)
                    )
                return web.json_response({'matches': [match for _, match in matches]})

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
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='[%(asctime)s %(levelname)s] [%(name)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

    async def on_startup(app: web.Application) -> None:
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
