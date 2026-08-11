"""
Spotify Web Scraping Module

This module scrapes metadata directly from Spotify's public embed widgets,
bypassing the need for Developer API credentials or a Premium account.

All regex operations are performed safely: every re.search() call checks
for a None match before calling .group(), preventing 'NoneType' AttributeErrors.
"""

import re
import json
import requests
from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_headers() -> Dict[str, str]:
    """Return browser-like headers for Spotify requests."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }


def extract_spotify_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the content type and ID from a Spotify URL.

    Supports track, playlist, and album URLs.
    Returns (None, None) if the URL is not a recognised Spotify content URL.

    Safe: uses re.search with an explicit None-check before calling .group().
    """
    patterns = {
        "track":    r"spotify\.com/(?:[a-z]{2}/)?track/([A-Za-z0-9]+)",
        "playlist": r"spotify\.com/(?:[a-z]{2}/)?playlist/([A-Za-z0-9]+)",
        "album":    r"spotify\.com/(?:[a-z]{2}/)?album/([A-Za-z0-9]+)",
    }

    for content_type, pattern in patterns.items():
        match = re.search(pattern, url)
        if match:
            return content_type, match.group(1)

    return None, None


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scrape_spotify_embed(content_type: str, content_id: str) -> Dict:
    """
    Fetch the Spotify embed page for a given content type/ID and extract
    the embedded ``__NEXT_DATA__`` JSON blob.

    Returns the ``entity`` sub-object from the JSON.
    Raises ValueError with a descriptive message when the page structure
    is unexpected (regex mismatch or missing JSON keys).
    """
    embed_url = f"https://open.spotify.com/embed/{content_type}/{content_id}"

    response = requests.get(embed_url, headers=get_headers(), timeout=15)
    if response.status_code != 200:
        raise Exception(
            f"Failed to fetch Spotify embed page (HTTP {response.status_code}): {embed_url}"
        )

    html = response.text

    # ------------------------------------------------------------------
    # Safe regex extraction — always check for None before calling .group()
    # ------------------------------------------------------------------
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)

    if match is None:
        raise ValueError(
            "Could not find '__NEXT_DATA__' script tag in the Spotify embed page. "
            "Spotify may have changed its page structure."
        )

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse '__NEXT_DATA__' JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # Navigate the JSON structure safely
    # ------------------------------------------------------------------
    try:
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Unexpected JSON structure in Spotify embed page (missing key: {exc}). "
            "Spotify may have changed its response format."
        ) from exc

    return entity


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_spotify_content_info(url: str) -> Dict:
    """
    Return metadata for any Spotify track, playlist, or album URL.

    Return value shape
    ------------------
    Single track::

        {
            "type": "single",
            "data": {
                "id": str,
                "title": str,
                "artist": str,
                "album": str,
                "duration": int,   # seconds
                "thumbnail": str | None
            }
        }

    Playlist / album::

        {
            "type": "playlist",
            "playlist_title": str,
            "items": [ {"id", "title", "artist", "album", "duration", "thumbnail"}, ... ],
            "count": int
        }
    """
    content_type, content_id = extract_spotify_id_from_url(url)
    if not content_type or not content_id:
        raise ValueError(
            f"Invalid or unrecognised Spotify URL: {url!r}. "
            "Expected a track, playlist, or album URL."
        )

    entity = scrape_spotify_embed(content_type, content_id)

    title    = entity.get("title", "Unknown")
    subtitle = entity.get("subtitle", "Unknown")

    # Cover art — multiple fallback layers
    cover_art_url: Optional[str] = None
    try:
        sources = entity.get("coverArt", {}).get("sources") or []
        if sources:
            cover_art_url = sources[0].get("url")
    except (AttributeError, IndexError):
        pass

    # ------------------------------------------------------------------
    # Single track
    # ------------------------------------------------------------------
    if content_type == "track":
        raw_duration = entity.get("duration", 0)
        try:
            duration_secs = int(raw_duration) // 1000
        except (TypeError, ValueError):
            duration_secs = 0

        return {
            "type": "single",
            "data": {
                "id":        content_id,
                "title":     title,
                "artist":    subtitle,
                "album":     "Unknown Album",
                "duration":  duration_secs,
                "thumbnail": cover_art_url,
            },
        }

    # ------------------------------------------------------------------
    # Playlist or album
    # ------------------------------------------------------------------
    track_list = entity.get("trackList") or []
    items = []

    for track in track_list:
        track_cover: Optional[str] = None
        try:
            track_sources = track.get("coverArt", {}).get("sources") or []
            if track_sources:
                track_cover = track_sources[0].get("url")
        except (AttributeError, IndexError):
            pass

        if track_cover is None:
            track_cover = cover_art_url  # Fall back to the collection cover art

        raw_dur = track.get("duration", 0)
        try:
            track_duration = int(raw_dur) // 1000
        except (TypeError, ValueError):
            track_duration = 0

        items.append({
            "id":       track.get("id", ""),
            "title":    track.get("title", "Unknown Title"),
            "artist":   track.get("subtitle", "Unknown Artist"),
            "album":    "Unknown Album",
            "duration": track_duration,
            "thumbnail": track_cover,
        })

    return {
        "type":           "playlist",
        "playlist_title": title,
        "items":          items,
        "count":          len(items),
    }
