#!/usr/bin/env python3
"""Central collector server for Linux Fault Agent.

Receives reports from fault-agent instances via POST /api/v1/reports
and stores them in SQLite for querying.

Zero dependencies beyond Python 3 standard library.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION = "1.0.0"
DEFAULT_PORT = 8000
DEFAULT_DB = "/var/lib/fault-agent-server/reports.db"
DEFAULT_BEARER_TOKEN_PATH = ""

log = logging.getLogger("fault-server")


class FaultServerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the fault agent collector server."""

    # Shared across all handler instances
    db_path: str = DEFAULT_DB
    bearer_token: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s - %s", self.client_address[0], format % args)

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _authenticate(self) -> bool:
        if self.bearer_token is None:
            return True  # no auth configured
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.bearer_token}"
        return auth == expected

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/v1/health":
            self._send_json(200, {"status": "ok", "version": VERSION})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/v1/reports":
            self._handle_report()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_report(self) -> None:
        if not self._authenticate():
            self._send_json(401, {"error": "unauthorized"})
            return

        try:
            body = self._read_body()
            report = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Validate required fields
        for field in ("hostname", "reported_at", "checks"):
            if field not in report:
                self._send_json(400, {"error": f"missing required field: {field}"})
                return

        # Store in database
        try:
            _store_report(self.db_path, report)
            log.info("stored report from %s (hostname=%s, %d checks)",
                     self.client_address[0], report["hostname"], len(report["checks"]))
            self._send_json(200, {
                "status": "accepted",
                "hostname": report["hostname"],
                "reported_at": report["reported_at"],
                "checks_count": len(report["checks"]),
            })
        except Exception as e:
            log.error("failed to store report: %s", e)
            self._send_json(500, {"error": "internal storage error"})

    def do_PUT(self) -> None:
        self._send_json(405, {"error": "method not allowed"})

    def do_DELETE(self) -> None:
        self._send_json(405, {"error": "method not allowed"})

    def do_PATCH(self) -> None:
        self._send_json(405, {"error": "method not allowed"})


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

def _init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT NOT NULL,
                machine_id TEXT DEFAULT '',
                sysinfo TEXT DEFAULT '',
                tags TEXT DEFAULT '{}',
                reported_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                uptime_seconds REAL DEFAULT 0,
                agent_version TEXT DEFAULT '',
                report_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reports_hostname
            ON reports(hostname)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reports_reported_at
            ON reports(reported_at)
        """)
        conn.commit()
    finally:
        conn.close()


def _store_report(db_path: str, report: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO reports
               (hostname, machine_id, sysinfo, tags, reported_at, received_at,
                uptime_seconds, agent_version, report_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.get("hostname", ""),
                report.get("machine_id", ""),
                report.get("sysinfo", ""),
                json.dumps(report.get("tags", {})),
                report.get("reported_at", ""),
                datetime.now(timezone.utc).isoformat(),
                report.get("uptime_seconds", 0),
                report.get("agent_version", ""),
                json.dumps(report),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def get_bearer_token(path: str) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text().strip()
    except Exception as e:
        log.warning("cannot read bearer token from %s: %s", path, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Fault Agent Collector Server")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT,
                        help=f"Listen port (default: {DEFAULT_PORT})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--bearer-token-path", default=DEFAULT_BEARER_TOKEN_PATH,
                        help="Path to bearer token file for simple auth")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")

    # Initialize database
    FaultServerHandler.db_path = args.db
    _init_db(args.db)
    log.info("database initialized at %s", args.db)

    # Auth
    token = get_bearer_token(args.bearer_token_path)
    FaultServerHandler.bearer_token = token
    if token:
        log.info("bearer token auth enabled")
    else:
        log.info("no bearer token configured, auth disabled")

    # Start server
    server = HTTPServer((args.bind, args.port), FaultServerHandler)
    log.info("fault-agent-server v%s listening on %s:%s", VERSION, args.bind, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down...")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()