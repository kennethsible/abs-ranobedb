import argparse
import re
from datetime import datetime
from typing import Any

import requests
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from langcodes import Language

app = Flask(__name__)

API_URL = 'https://ranobedb.org/api/v0'


def extract_author(book_data: dict[str, Any]) -> str:
    author = ''
    author_names = []
    for edition in book_data.get('editions', []):
        for staff in edition.get('staff', []):
            if staff.get('role_type') in ['author', 'artist']:
                name = staff.get('romaji') or staff.get('name')
                if name and name not in author_names:
                    author_names.append(name)
    if author_names:
        author = ', '.join(author_names)
    return author


def extract_series_name(book_data: dict[str, Any]) -> str:
    series_name = ''
    series_data = book_data.get('series', {})
    if series_data:
        series_name = series_data.get('title') or series_data.get('romaji') or ''
    return series_name


# def extract_sequence(title: str) -> str:
#     sequence = ''
#     vol_match = re.search(r'(?i)\b(?:vol|volume|v)\.?\s*(\d+(\.\d+)?)', title)
#     if vol_match:
#         sequence = vol_match.group(1)
#     return sequence


def extract_sequence(book_data: dict[str, Any], book_id: int) -> str:
    series_data = book_data.get('series', {})
    for index, book in enumerate(series_data.get('books', [])):
        if book.get('id') == book_id:
            return str(index + 1)
    return ''


def extract_description(book_data: dict[str, Any]) -> str:
    description = book_data.get('description', '').strip()
    description = re.sub(r'\s*\[From\s+.*\]\s*$', '', description)
    return description.strip()


def extract_genres(book_data: dict[str, Any]) -> list[str]:
    genres = []
    series_data = book_data.get('series', {})
    for tag in series_data.get('tags', []):
        tag_name = tag.get('name', '')
        if tag_name and tag.get('ttype') == 'genre':
            genres.append(tag_name.title())
    return genres


def extract_publisher(book_data: dict[str, Any], lang_tag: str) -> str:
    publisher = ''
    publishers = book_data.get('publishers', [])
    sorted_publishers = sorted(publishers, key=lambda x: 0 if x.get('lang') == lang_tag else 1)
    if sorted_publishers:
        publisher = sorted_publishers[0].get('name', '')
    return publisher


def extract_year(book_data: dict[str, Any], lang_tag: str, book_date: int | None = None) -> str:
    release_year = ''
    release_dates = book_data.get('c_release_dates', {})
    release_date = release_dates.get(lang_tag) or book_data.get('c_release_date') or book_date
    if release_date:
        if release_date > 30000000:
            release_year = str(datetime.fromtimestamp(release_date).year)
        else:
            release_year = str(release_date)[:4]
    return release_year


def extract_cover(book_data: dict[str, Any]) -> str:
    cover_url = ''
    image_data = book_data.get('image', {})
    if image_data and image_data.get('filename'):
        cover_url = f'https://images.ranobedb.org/{image_data["filename"]}'
    return cover_url


def extract_isbn(book_data: dict[str, Any], lang_tag: str) -> str:
    isbn = ''
    releases = book_data.get('releases', [])
    sorted_releases = sorted(releases, key=lambda x: 0 if x.get('lang') == lang_tag else 1)
    for release in sorted_releases:
        if not isbn and release.get('isbn13'):
            isbn = release.get('isbn13')
    return isbn


def retrieve_matches(data: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for book in data.get('books', []):
        book_data: dict[str, Any] = {}
        book_id = book.get('id')
        try:
            response = requests.get(f'{API_URL}/book/{book_id}')
            response.raise_for_status()
            book_data = response.json().get('book', {})
        except requests.RequestException:
            pass

        lang_tag = book_data.get('lang', book.get('lang', ''))
        language = Language.get(lang_tag).display_name() if lang_tag else ''
        title = book.get('title') or book.get('romaji') or book.get('title_orig') or ''

        series: list[dict[str, Any]] = []
        if series_name := extract_series_name(book_data):
            sequence = extract_sequence(book_data, book_id)
            series.append({'series': series_name, 'sequence': sequence})

        matches.append(
            {
                'title': title,
                'author': extract_author(book_data),
                'series': series,
                'description': extract_description(book_data),
                'genres': extract_genres(book_data),
                'publisher': extract_publisher(book_data, lang_tag),
                'publishedYear': extract_year(book_data, lang_tag, book.get('c_release_date')),
                'language': language,
                'cover': extract_cover(book_data),
                'isbn': extract_isbn(book_data, lang_tag),
            }
        )
    return matches


@app.route('/search', methods=['GET'])
def search() -> ResponseReturnValue:
    query = request.args.get('query')
    if not query:
        return jsonify({'error': 'empty query'}), 400
    try:
        params: dict[str, str | int] = {'q': query, 'limit': 5}
        response = requests.get(f'{API_URL}/books', params=params)
        response.raise_for_status()
        matches = retrieve_matches(response.json())
        return jsonify({'matches': matches})
    except (requests.RequestException, ValueError) as e:
        return jsonify({'error': str(e)}), 502


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()

    app.run(host=args.host, port=args.port)


if __name__ == '__main__':
    main()
