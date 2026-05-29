"""
Trigger a GitHub Actions workflow dispatch to refresh signal data.

POST /api/refresh → dispatches the signal-alerts workflow on GitHub.
The workflow fetches prices, detects signals, commits JSON, and Vercel
auto-deploys. Takes ~2 minutes end-to-end.

Requires GITHUB_PAT environment variable (repo scope, workflow permission).
"""

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


GITHUB_REPO = "utoraman/rapidsift"
WORKFLOW_FILE = "signal-alerts.yml"


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        token = os.environ.get("GITHUB_PAT")
        if not token:
            self._respond(500, {"error": "GITHUB_PAT not configured"})
            return

        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        body = json.dumps({"ref": "main"}).encode()

        req = Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")

        try:
            urlopen(req, timeout=10)
            self._respond(200, {
                "status": "triggered",
                "message": "Signal refresh dispatched. Data updates in ~2 minutes."
            })
        except HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            self._respond(e.code, {
                "error": f"GitHub API error: {e.code}",
                "detail": error_body[:200]
            })
        except URLError as e:
            self._respond(502, {"error": f"Network error: {str(e)}"})

    def do_GET(self):
        """Allow GET for easy testing."""
        self.do_POST()

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
