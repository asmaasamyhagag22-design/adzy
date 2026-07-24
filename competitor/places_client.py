"""Minimal Google Places API (New) client.

Endpoints (verified against the current Places API New, field masks required):
  - Nearby Search : POST  https://places.googleapis.com/v1/places:searchNearby
  - Text Search   : POST  https://places.googleapis.com/v1/places:searchText
  - Place Details : GET   https://places.googleapis.com/v1/places/{PLACE_ID}
  - Geocoding     : GET   https://maps.googleapis.com/maps/api/geocode/json   (legacy, still active)

Field-mask rules: for search the paths are prefixed with `places.`; for Place
Details they are NOT prefixed. Reviews are capped at 5 per place by Google.

The API key is read from the environment (GOOGLE_MAPS_API_KEY by default). The
key is never logged and never placed in a URL query string for the Places calls
(it goes in the X-Goog-Api-Key header). You must supply your own key with the
Places API (New) and Geocoding API enabled and billing configured.

Requires: requests  (pip install requests)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .models import Candidate, Review

_PLACES_BASE = "https://places.googleapis.com/v1"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Fields requested from search calls (Basic + a little Atmosphere). Keep this lean
# to control billing — every extra SKU-tier field costs more.
_SEARCH_FIELD_MASK = ",".join(
    "places." + f for f in [
        "id", "displayName", "primaryType", "types", "location",
        "rating", "userRatingCount", "websiteUri", "businessStatus",
        "priceLevel", "formattedAddress", "googleMapsUri",
    ]
)
# Place Details mask for the reviews pass (no `places.` prefix here).
_REVIEWS_FIELD_MASK = "reviews,rating,userRatingCount"


class PlacesError(RuntimeError):
    pass


def _load_dotenv() -> None:
    """Zero-dependency .env loader: walk up from the current directory, read
    KEY=VALUE lines from the first .env found, and put them into os.environ
    WITHOUT overwriting anything already set. Lets you keep the key in a .env at
    your project root instead of re-exporting it every PowerShell session."""
    cur = Path.cwd()
    for d in (cur, *cur.parents):
        f = d / ".env"
        if f.is_file():
            try:
                for line in f.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except OSError:
                pass
            break


class PlacesClient:
    def __init__(self, api_key: Optional[str] = None, *,
                 timeout: int = 15, max_retries: int = 3,
                 language_code: str = "", region_code: str = ""):
        # P-81 (D-8): defaults are NEUTRAL now, not "ar"/"EG". The old defaults meant every
        # competitor search for a Saudi/Qatari/US brand was run in the EGYPTIAN market in Arabic
        # (both live entrypoints used PlacesClient() with no args). The caller passes the brand's
        # OWN evidenced market (poster.locale.search_locale); when unknown, empty -> omitted from
        # the request -> Google's own default, never a guessed (wrong) market.
        if not api_key:
            _load_dotenv()
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY") \
            or os.environ.get("GOOGLE_PLACES_API_KEY")
        if not self.api_key:
            raise PlacesError(
                "No API key found. Either:\n"
                "  - put a line  GOOGLE_MAPS_API_KEY=your_key  in a .env file at your "
                "project root (C:\\Users\\Admin\\asmaa\\scraper_v01\\.env), or\n"
                "  - set it for the current PowerShell session:\n"
                "      $env:GOOGLE_MAPS_API_KEY = \"your_key\"\n"
                "  (do not hard-code it in source)."
            )
        self.timeout = timeout
        self.max_retries = max_retries
        self.language_code = language_code
        self.region_code = region_code
        self.last_geocode_error: Optional[str] = None
        self._session = requests.Session()

    def _locale_body(self) -> dict:
        """languageCode/regionCode for a request body, OMITTED when empty (P-81) — an empty market
        is 'no bias', never a wrong one, byte-identical to a pre-D-8 request that guessed nothing."""
        out: dict = {}
        if self.language_code:
            out["languageCode"] = self.language_code
        if self.region_code:
            out["regionCode"] = self.region_code
        return out

    # -- low-level POST with simple backoff on 429/5xx --
    def _post(self, url: str, body: dict, field_mask: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }
        for attempt in range(self.max_retries):
            resp = self._session.post(url, json=body, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise PlacesError(f"{resp.status_code} from {url}: {resp.text[:300]}")
        raise PlacesError(f"exhausted retries for {url}")

    def _get(self, url: str, params: dict, field_mask: Optional[str]) -> dict:
        headers = {"X-Goog-Api-Key": self.api_key}
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        for attempt in range(self.max_retries):
            resp = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise PlacesError(f"{resp.status_code} from {url}: {resp.text[:300]}")
        raise PlacesError(f"exhausted retries for {url}")

    # ----------------------------------------------------------------- geocode
    def geocode_via_places(self, query: str) -> Optional[Tuple[float, float]]:
        """Resolve an address / place name to (lat, lng) using Places Text Search.

        This is the PREFERRED geocoder: it needs only the Places API (New) to be
        enabled, not the separate Geocoding API. We just need an approximate
        center point for the nearby search, so the first match is good enough.
        """
        body = {"textQuery": query, **self._locale_body()}
        data = self._post(f"{_PLACES_BASE}/places:searchText", body,
                          "places.location,places.formattedAddress,places.displayName")
        places = data.get("places") or []
        if not places:
            return None
        loc = places[0].get("location") or {}
        if "latitude" in loc and "longitude" in loc:
            return float(loc["latitude"]), float(loc["longitude"])
        return None

    def geocode_address(self, address: str) -> Optional[Tuple[float, float]]:
        """Address string -> (lat, lng) via the (separate) Geocoding API. Returns
        None on failure and stores the reason in self.last_geocode_error so the
        caller can tell 'API not enabled' apart from 'no results'."""
        params = {"address": address, "key": self.api_key}
        if self.region_code:                                   # P-81: omit when no evidenced market
            params["region"] = self.region_code
        data = self._get(_GEOCODE_URL, params, field_mask=None)
        status = data.get("status")
        if status == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            self.last_geocode_error = None
            return float(loc["lat"]), float(loc["lng"])
        self.last_geocode_error = f"{status}: {data.get('error_message', '')}".strip(": ")
        return None

    # ------------------------------------------------------------ candidate fetch
    @staticmethod
    def _to_candidate(p: dict) -> Candidate:
        loc = p.get("location") or {}
        return Candidate(
            place_id=p.get("id", ""),
            name=(p.get("displayName") or {}).get("text", ""),
            primary_type=p.get("primaryType"),
            types=p.get("types") or [],
            lat=loc.get("latitude"),
            lng=loc.get("longitude"),
            rating=p.get("rating"),
            review_count=p.get("userRatingCount"),
            website=p.get("websiteUri"),
            business_status=p.get("businessStatus"),
            price_level=p.get("priceLevel"),
            formatted_address=p.get("formattedAddress"),
            maps_uri=p.get("googleMapsUri"),
        )

    def search_nearby(self, lat: float, lng: float, included_types: List[str], *,
                      radius_m: float = 5000, max_results: int = 20,
                      rank: str = "POPULARITY") -> List[Candidate]:
        body = {
            "includedTypes": included_types,
            "maxResultCount": min(max_results, 20),  # Nearby Search caps at 20
            "rankPreference": rank,                  # "POPULARITY" or "DISTANCE"
            **self._locale_body(),                   # P-81: brand's market, omitted when unknown
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
            },
        }
        data = self._post(f"{_PLACES_BASE}/places:searchNearby", body, _SEARCH_FIELD_MASK)
        return [self._to_candidate(p) for p in (data.get("places") or [])]

    def search_text(self, text_query: str, *, included_type: Optional[str] = None,
                    lat: Optional[float] = None, lng: Optional[float] = None,
                    radius_m: float = 5000, max_results: int = 20) -> List[Candidate]:
        """For online-only businesses (no useful location) or category text search.
        Supports nextPageToken pagination up to `max_results`."""
        body: Dict = {"textQuery": text_query, **self._locale_body()}
        if included_type:
            body["includedType"] = included_type
        if lat is not None and lng is not None:
            body["locationBias"] = {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
            }

        out: List[Candidate] = []
        page_token = None
        while len(out) < max_results:
            if page_token:
                body["pageToken"] = page_token
            data = self._post(f"{_PLACES_BASE}/places:searchText", body, _SEARCH_FIELD_MASK)
            out.extend(self._to_candidate(p) for p in (data.get("places") or []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            time.sleep(1.0)  # token needs a brief moment to become valid
        return out[:max_results]

    # --------------------------------------------------------------- reviews
    def get_reviews(self, place_id: str) -> List[Review]:
        """Up to 5 reviews for a place (Google's hard cap)."""
        url = f"{_PLACES_BASE}/places/{place_id}"
        params = {"languageCode": self.language_code} if self.language_code else {}  # P-81
        data = self._get(url, params=params, field_mask=_REVIEWS_FIELD_MASK)
        reviews: List[Review] = []
        for r in (data.get("reviews") or []):
            text = (r.get("text") or {}).get("text") \
                or (r.get("originalText") or {}).get("text") or ""
            reviews.append(Review(
                rating=r.get("rating"),
                text=text,
                author=(r.get("authorAttribution") or {}).get("displayName"),
                relative_time=r.get("relativePublishTimeDescription"),
                language_code=(r.get("text") or {}).get("languageCode")
                or (r.get("originalText") or {}).get("languageCode"),
            ))
        return reviews
