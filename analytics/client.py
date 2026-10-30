"""Clients that read data from the blood bank API.

HttpApiClient  - calls a running server over HTTP, e.g.
                 HttpApiClient("http://127.0.0.1:8000", api_key="...")
LocalApiClient - calls the very same API views inside this process, so
                 training also works when no server is running.

Both follow the API's pagination ("next" links) and return a list of records.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.test import RequestFactory
from django.urls import resolve


class ApiError(Exception):
    pass


class HttpApiClient:
    def __init__(self, base_url, api_key=None, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or settings.ANALYTICS_API_KEY
        self.timeout = timeout

    @property
    def source_name(self):
        return self.base_url

    def _get(self, url):
        request = urllib.request.Request(url, headers={"X-API-Key": self.api_key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise ApiError(f"GET {url} failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ApiError(f"Cannot reach {url}: {error.reason}") from error

    def fetch_all(self, path, params=None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        records = []
        while url:
            page = self._get(url)
            records.extend(page["results"])
            url = f"{self.base_url}{page['next']}" if page.get("next") else None
        return records

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="POST",
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


class LocalApiClient:
    """Calls the API views in-process (no network, same responses)."""

    def __init__(self, api_key=None):
        self.api_key = api_key or settings.ANALYTICS_API_KEY
        self.factory = RequestFactory()

    @property
    def source_name(self):
        return "in-process /api/v1"

    def _call(self, request):
        match = resolve(request.path)
        response = match.func(request, *match.args, **match.kwargs)
        return response.status_code, json.loads(response.content)

    def fetch_all(self, path, params=None):
        params = dict(params or {})
        records = []
        url = path + ("?" + urllib.parse.urlencode(params) if params else "")
        while url:
            parsed = urllib.parse.urlsplit(url)
            query = dict(urllib.parse.parse_qsl(parsed.query))
            status, page = self._call(self.factory.get(parsed.path, query, HTTP_X_API_KEY=self.api_key))
            if status != 200:
                raise ApiError(f"GET {url} failed with HTTP {status}: {page}")
            records.extend(page["results"])
            url = page.get("next")
        return records

    def post(self, path, payload):
        request = self.factory.post(
            path, data=json.dumps(payload), content_type="application/json", HTTP_X_API_KEY=self.api_key
        )
        return self._call(request)


def get_client(api_url=None, api_key=None):
    return HttpApiClient(api_url, api_key) if api_url else LocalApiClient(api_key)
