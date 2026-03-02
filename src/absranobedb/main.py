import argparse
import asyncio
import re
from datetime import datetime
from typing import Any

import aiohttp
from aiohttp import web
from langcodes import Language

API_URL = 'https://ranobedb.org/api/v0'


def extract_author(details: dict[str, Any]) -> str:
    author_names = []
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
    series_data = details.get('series', {})
    if series_data:
        return series_data.get('title') or series_data.get('romaji') or ''
    return ''


def extract_sequence(details: dict[str, Any], book_id: int) -> str:
    series_details = details.get('series', {})
    for index, book_details in enumerate(series_details.get('books', [])):
        if book_details.get('id') == book_id:
            return str(index + 1)
    return ''


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
    sorted_publishers = sorted(publishers, key=lambda x: 0 if x.get('lang') == tag else 1)
    if sorted_publishers:
        return str(sorted_publishers[0].get('name', ''))
    return ''


def extract_year(details: dict[str, Any], tag: str, date: int | None = None) -> str:
    release_dates = details.get('c_release_dates', {})
    release_date = release_dates.get(tag) or details.get('c_release_date') or date
    if release_date:
        if release_date > 30000000:
            return str(datetime.fromtimestamp(release_date).year)
        else:
            return str(release_date)[:4]
    return ''


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


async def fetch_book_data(book_id: int, session: aiohttp.ClientSession) -> dict[str, Any]:
    try:
        async with session.get(f'{API_URL}/book/{book_id}') as response:
            response.raise_for_status()
            data = await response.json()
            if isinstance(data, dict):
                summary = data.get('book', {})
                if isinstance(summary, dict):
                    return summary
            return {}
    except aiohttp.ClientError:
        return {}


async def extract_metadata(
    summary: dict[str, Any], session: aiohttp.ClientSession
) -> dict[str, Any]:
    details = {}
    if book_id := summary.get('id'):
        details = await fetch_book_data(int(book_id), session)

    tag = details.get('lang', summary.get('lang', ''))
    language = Language.get(tag).display_name() if tag else ''
    title = summary.get('title') or summary.get('romaji') or summary.get('title_orig') or ''

    series: list[dict[str, Any]] = []
    if book_id and (series_name := extract_series_name(details)):
        sequence = extract_sequence(details, book_id)
        series.append({'series': series_name, 'sequence': sequence})

    return {
        'title': title,
        'subtitle': details.get('subtitle', ''),
        'author': extract_author(details),
        'series': series,
        'description': extract_description(details),
        'genres': extract_genres(details),
        'publisher': extract_publisher(details, tag),
        'publishedYear': extract_year(details, tag, summary.get('c_release_date')),
        'language': language,
        'cover': extract_cover(details),
        'isbn': extract_isbn(details, tag),
    }


async def gather_matches(
    data: dict[str, Any], session: aiohttp.ClientSession
) -> list[dict[str, Any]]:
    tasks = [extract_metadata(summary, session) for summary in data.get('books', [])]
    return await asyncio.gather(*tasks)


async def search(request: web.Request) -> web.Response:
    query = request.query.get('query')
    if not query:
        return web.json_response({'error': 'empty query'}, status=400)
    try:
        params = {'q': query, 'limit': '5'}
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{API_URL}/books', params=params) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    return web.json_response({'error': 'invalid upstream response'}, status=502)
                matches = await gather_matches(data, session)
                return web.json_response({'matches': matches})
    except aiohttp.ClientResponseError as e:
        return web.json_response({'error': f'upstream API error: {e}'}, status=502)
    except aiohttp.ClientError as e:
        return web.json_response({'error': f'upstream connection failed: {e}'}, status=502)
    except asyncio.TimeoutError:
        return web.json_response({'error': 'upstream API timed out'}, status=504)
    except ValueError as e:
        return web.json_response({'error': f'invalid upstream response: {e}'}, status=502)
    except Exception as e:
        return web.json_response({'error': f'internal server error: {e}'}, status=500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get('/search', search)

    web.run_app(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
