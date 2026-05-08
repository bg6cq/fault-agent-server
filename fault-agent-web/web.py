#!/usr/bin/env python3
"""Web dashboard for Fault Agent — view host status, check results, and history.

Reads from the same SQLite database as fault-agent-server.
Zero dependencies beyond Python 3 standard library.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION = "1.0.0"
DEFAULT_PORT = 9000
DEFAULT_DB = "/var/lib/fault-agent-server/reports.db"

log = logging.getLogger("fault-web")

# ── Status colours ────────────────────────────────────────────────

STATUS_COLOURS = {
    "ok": "#22c55e",
    "warning": "#f59e0b",
    "critical": "#ef4444",
    "error": "#8b5cf6",
}

STATUS_BG = {
    "ok": "#f0fdf4",
    "warning": "#fffbeb",
    "critical": "#fef2f2",
    "error": "#faf5ff",
}


def _count_status(summary: dict, key: str) -> int:
    return summary.get(key, 0) if summary else 0


# ── DB helpers ────────────────────────────────────────────────────

def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_hosts(db_path: str) -> list[dict]:
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT r1.hostname, r1.reported_at, r1.uptime_seconds, r1.agent_version,
                   r1.sysinfo, r1.tags, r1.report_json
            FROM reports r1
            INNER JOIN (
                SELECT hostname, MAX(reported_at) AS max_ts
                FROM reports
                GROUP BY hostname
            ) r2 ON r1.hostname = r2.hostname AND r1.reported_at = r2.max_ts
            ORDER BY r1.hostname
        """).fetchall()
        result = []
        for row in rows:
            report = json.loads(row["report_json"])
            summary = report.get("summary", {})
            result.append({
                "hostname": row["hostname"],
                "sysinfo": row["sysinfo"],
                "tags": json.loads(row["tags"]) if row["tags"] else {},
                "reported_at": row["reported_at"],
                "uptime_seconds": row["uptime_seconds"],
                "agent_version": row["agent_version"],
                "summary": summary,
                "total_checks": report.get("check_interval_seconds", 0),
            })
        return result
    finally:
        conn.close()


def get_host_history(db_path: str, hostname: str, limit: int = 30) -> list[dict]:
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT reported_at, report_json FROM reports WHERE hostname = ? ORDER BY reported_at DESC LIMIT ?",
            (hostname, limit),
        ).fetchall()
        return [{"reported_at": r["reported_at"], "report": json.loads(r["report_json"])} for r in rows]
    finally:
        conn.close()


def get_latest_report(db_path: str, hostname: str) -> dict | None:
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT report_json FROM reports WHERE hostname = ? ORDER BY reported_at DESC LIMIT 1",
            (hostname,),
        ).fetchone()
        return json.loads(row["report_json"]) if row else None
    finally:
        conn.close()


def get_recent_reports(db_path, minutes=10):
    """Get reports from the last N minutes across all hosts."""
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT hostname, reported_at, report_json FROM reports
               WHERE received_at >= datetime('now', ? || ' minutes', 'utc')
               ORDER BY received_at DESC""",
            (f"-{minutes}",),
        ).fetchall()
        return [{"hostname": r["hostname"], "report": json.loads(r["report_json"])} for r in rows]
    finally:
        conn.close()


def get_report_by_timestamp(db_path, hostname, reported_at):
    """Get a specific report by hostname and reported_at timestamp."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT report_json FROM reports WHERE hostname = ? AND reported_at = ? LIMIT 1",
            (hostname, reported_at),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT report_json FROM reports WHERE hostname = ? AND reported_at LIKE ? ORDER BY reported_at DESC LIMIT 1",
                (hostname, reported_at + "%"),
            ).fetchone()
        return json.loads(row["report_json"]) if row else None
    finally:
        conn.close()
    try:
        rows = conn.execute(
            """SELECT hostname, reported_at, report_json FROM reports
               WHERE received_at >= datetime('now', ? || ' minutes', 'utc')
               ORDER BY received_at DESC""",
            (f"-{minutes}",),
        ).fetchall()
        return [{"hostname": r["hostname"], "report": json.loads(r["report_json"])} for r in rows]
    finally:
        conn.close()


# ── HTTP Handler ──────────────────────────────────────────────────

PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fault Agent Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#f8fafc; color:#1e293b; font-size:14px; line-height:1.5; }
  .container { max-width:1200px; margin:0 auto; padding:20px; }
  nav { background:#1e293b; color:#fff; padding:12px 20px; display:flex; align-items:center; gap:16px; }
  nav a { color:#94a3b8; text-decoration:none; font-weight:500; }
  nav a:hover { color:#fff; }
  nav .brand { font-size:18px; font-weight:700; color:#fff; margin-right:auto; }
  h1 { font-size:22px; margin:24px 0 16px; }
  h2 { font-size:17px; margin:20px 0 12px; }
  .card { background:#fff; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.08);
          padding:16px; margin-bottom:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; }
  .stat-row { display:flex; gap:12px; flex-wrap:wrap; }
  .stat { text-align:center; min-width:72px; padding:8px 12px; border-radius:6px; }
  .stat .num { font-size:24px; font-weight:700; }
  .stat .lbl { font-size:11px; color:#64748b; text-transform:uppercase; }
  .tag { display:inline-block; background:#e2e8f0; border-radius:4px; padding:2px 8px; font-size:12px; margin:2px; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #f1f5f9; }
  tr.clickable:hover { background:#f8fafc; }
  th { font-size:11px; text-transform:uppercase; color:#64748b; font-weight:600; }
  .status-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }
  .host-card { text-decoration:none; color:inherit; display:block; transition:box-shadow .15s; }
  .host-card:hover { box-shadow:0 4px 12px rgba(0,0,0,.12); }
  .host-card .top { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px; }
  .host-card .hostname { font-size:16px; font-weight:600; }
  .host-card .meta { font-size:12px; color:#64748b; }
  .uptime-bar { height:4px; border-radius:2px; background:#e2e8f0; margin-top:8px; overflow:hidden; }
  .uptime-bar-fill { height:100%; background:#22c55e; border-radius:2px; }
  .check-row { display:flex; align-items:center; gap:8px; padding:6px 0; }
  .check-row .name { flex:1; }
  .check-row .msg { color:#64748b; font-size:12px; max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pulse { animation:pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  .badge { display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:10px; }
  .badge-ok { background:#dcfce7; color:#166534; }
  .badge-warning { background:#fef3c7; color:#92400e; }
  .badge-critical { background:#fee2e2; color:#991b1b; }
  .badge-error { background:#ede9fe; color:#5b21b6; }
  .back { display:inline-flex; align-items:center; gap:4px; color:#64748b; text-decoration:none; font-size:13px; margin-bottom:12px; }
  .back:hover { color:#1e293b; }
  .empty { text-align:center; padding:40px; color:#94a3b8; }
  .empty h2 { margin-bottom:8px; color:#64748b; }
  pre.detail { background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px; font-size:12px; overflow-x:auto; max-height:200px; }
  footer { text-align:center; padding:24px; color:#94a3b8; font-size:12px; }
  @media(max-width:640px){ .grid{grid-template-columns:1fr} }
</style>
</head>
<body>
<nav>
  <a class="brand" href="/">Fault Agent</a>
  <a href="/">Dashboard</a>
  <span style="font-size:12px;color:#64748b;">v""" + VERSION + """</span>
</nav>
<div class="container">
"""

PAGE_FOOT = """
</div>
<footer>Fault Agent Dashboard v""" + VERSION + """ — data from SQLite</footer>
</body>
</html>"""


class WebHandler(BaseHTTPRequestHandler):
    db_path: str = DEFAULT_DB

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.client_address[0], fmt % args)

    def _send_html(self, body: str, code: int = 200) -> None:
        html_bytes = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(html_bytes)

    def _send_json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _render_time(self, iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str)
            local = dt.replace(tzinfo=timezone.utc).astimezone()
            return local.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return iso_str

    def _render_uptime(self, seconds: float) -> str:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h"

    def _status_class(self, status: str) -> str:
        return {
            "ok": "badge-ok",
            "warning": "badge-warning",
            "critical": "badge-critical",
            "error": "badge-error",
        }.get(status, "")

    # ── Routes ──

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._handle_index()
        elif path.startswith("/host/"):
            hostname = path[6:]
            self._handle_host(hostname)
        elif path == "/api/hosts":
            self._send_json(get_hosts(self.db_path))
        elif path.startswith("/report/"):
            # /report/<hostname>/<reported_at>
            parts = path.split("/")
            if len(parts) >= 4:
                r_hostname = parts[2]
                r_ts = "/".join(parts[3:])  # timestamp may contain /
                self._handle_report(r_hostname, r_ts)
            else:
                self._send_html(self._page_not_found(), 404)
        elif path.startswith("/api/host/"):
            hostname = path[10:]
            report = get_latest_report(self.db_path, hostname)
            if report:
                self._send_json(report)
            else:
                self._send_json({"error": "not found"}, 404)
        elif path.startswith("/api/history/"):
            hostname = path[13:]
            history = get_host_history(self.db_path, hostname)
            self._send_json(history)
        else:
            self._send_html(self._page_not_found(), 404)

    # ── Pages ──

    def _page_not_found(self) -> str:
        return PAGE_HEAD + '<div class="empty"><h2>404</h2><p>Page not found</p></div>' + PAGE_FOOT

    def _handle_index(self) -> None:
        hosts = get_hosts(self.db_path)
        total = len(hosts)

        # Aggregate
        total_crit = sum(_count_status(h["summary"], "critical") for h in hosts)
        total_warn = sum(_count_status(h["summary"], "warning") for h in hosts)
        total_err = sum(_count_status(h["summary"], "error") for h in hosts)

        # Recent activity (last 10 minutes)
        try:
            recent = get_recent_reports(self.db_path, 10)
        except Exception:
            recent = []

        parts = [PAGE_HEAD]

        # Title
        parts.append('<h1>Dashboard</h1>')

        # Global stats bar
        parts.append(f'''<div class="card">
          <div class="stat-row">
            <div class="stat"><div class="num">{total}</div><div class="lbl">Hosts</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['critical']}">{total_crit}</div><div class="lbl">Critical</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['warning']}">{total_warn}</div><div class="lbl">Warning</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['error']}">{total_err}</div><div class="lbl">Error</div></div>
            <div class="stat"><div class="num">{len(recent)}</div><div class="lbl">Active (10m)</div></div>
          </div>
        </div>''')

        if not hosts:
            parts.append('<div class="empty"><h2>No hosts</h2><p>Waiting for first report...</p></div>')
        else:
            parts.append('<div class="grid">')
            for h in hosts:
                s = h["summary"] or {}
                crit = _count_status(s, "critical")
                warn = _count_status(s, "warning")
                err = _count_status(s, "error")
                ok_count = _count_status(s, "ok")

                # Determine overall status colour
                if crit:
                    dot = STATUS_COLOURS["critical"]
                elif err:
                    dot = STATUS_COLOURS["error"]
                elif warn:
                    dot = STATUS_COLOURS["warning"]
                else:
                    dot = STATUS_COLOURS["ok"]

                tags = h.get("tags", {}) or {}
                tag_html = ""
                if isinstance(tags, dict):
                    tag_html = "".join(f'<span class="tag">{html.escape(k)}={html.escape(v)}</span>'
                                       for k, v in tags.items())
                sysinfo = h.get("sysinfo", "") or ""
                sysinfo_html = f'<span class="tag">{html.escape(sysinfo)}</span>' if sysinfo else ""

                uptime = h.get("uptime_seconds", 0)
                uptime_pct = min(100, uptime / 86400)  # scale to 100% = 1 day

                parts.append(f'''<a class="host-card card" href="/host/{html.escape(h['hostname'])}">
                  <div class="top">
                    <div>
                      <div class="hostname"><span class="status-dot" style="background:{dot}"></span>{html.escape(h['hostname'])}</div>
                      <div class="meta">{self._render_time(h['reported_at'])} &middot; up {self._render_uptime(uptime)}</div>
                    </div>
                    <div class="stat-row">
                      <div class="stat" style="padding:4px 8px"><div class="num" style="font-size:18px;color:{STATUS_COLOURS['critical']}">{crit}</div><div class="lbl">CRIT</div></div>
                      <div class="stat" style="padding:4px 8px"><div class="num" style="font-size:18px;color:{STATUS_COLOURS['warning']}">{warn}</div><div class="lbl">WARN</div></div>
                      <div class="stat" style="padding:4px 8px"><div class="num" style="font-size:18px">{ok_count}</div><div class="lbl">OK</div></div>
                    </div>
                  </div>
                  {sysinfo_html}{tag_html}
                  <div class="uptime-bar"><div class="uptime-bar-fill" style="width:{uptime_pct:.0f}%"></div></div>
                </a>''')
            parts.append('</div>')

        parts.append(PAGE_FOOT)
        self._send_html("".join(parts))

    def _handle_host(self, hostname: str) -> None:
        report = get_latest_report(self.db_path, hostname)
        if not report:
            self._send_html(PAGE_HEAD + f'<div class="empty"><h2>Not found</h2><p>Host "{html.escape(hostname)}" not found</p></div>' + PAGE_FOOT, 404)
            return

        # History for timeline
        history = get_host_history(self.db_path, hostname, 50)

        parts = [PAGE_HEAD]

        # Back link
        parts.append('<a class="back" href="/">&larr; Dashboard</a>')

        # Host header
        s = report.get("summary", {}) or {}
        crit = _count_status(s, "critical")
        warn = _count_status(s, "warning")
        err = _count_status(s, "error")
        ok_count = _count_status(s, "ok")

        tags = report.get("tags", {}) or {}
        tag_html = ""
        if isinstance(tags, dict):
            tag_html = "".join(f'<span class="tag">{html.escape(k)}={html.escape(v)}</span>'
                               for k, v in tags.items())
        sysinfo = report.get("sysinfo", "") or ""
        sysinfo_html = f'<span class="tag">{html.escape(sysinfo)}</span>' if sysinfo else ""

        uptime = report.get("uptime_seconds", 0)
        machine_id = report.get("machine_id", "")

        parts.append(f'''<h1><span class="status-dot" style="background:{STATUS_COLOURS['critical'] if crit else STATUS_COLOURS['warning'] if warn else STATUS_COLOURS['error'] if err else STATUS_COLOURS['ok']};"></span>{html.escape(hostname)}</h1>''')

        parts.append(f'''<div class="card">
          <div class="stat-row" style="margin-bottom:12px">
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['critical']}">{crit}</div><div class="lbl">Critical</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['warning']}">{warn}</div><div class="lbl">Warning</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['error']}">{err}</div><div class="lbl">Error</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['ok']}">{ok_count}</div><div class="lbl">OK</div></div>
          </div>
          <div style="font-size:12px;color:#64748b;display:flex;gap:16px;flex-wrap:wrap;">
            <span>Agent: v{html.escape(report.get('agent_version',''))}</span>
            <span>Reported: {self._render_time(report['reported_at'])}</span>
            <span>Uptime: {self._render_uptime(uptime)}</span>
            <span>Interval: {report.get('check_interval_seconds',0)}s</span>
            <span>Machine: <code>{html.escape(machine_id[:16])}…</code></span>
          </div>
          <div style="margin-top:8px">{sysinfo_html}{tag_html}</div>
        </div>''')

        # Checks table
        checks = report.get("checks", [])
        parts.append('<h2>Check Results</h2>')
        parts.append('<div class="card"><table><thead><tr><th>Status</th><th>Check</th><th>Message</th><th>Value</th><th>Threshold</th></tr></thead><tbody>')

        # Sort: critical/warning/error first, then by name
        def sort_key(c):
            prio = {"critical": 0, "error": 1, "warning": 2, "ok": 3}
            return (prio.get(c["status"], 9), c["check_name"])

        for c in sorted(checks, key=sort_key):
            st = c["status"]
            colour = STATUS_COLOURS.get(st, "#94a3b8")
            cls = self._status_class(st)
            val = c.get("metric_value")
            val_str = f'{val} {c.get("metric_unit","")}'.strip() if val is not None else ""
            thr = c.get("threshold")
            thr_str = f"{thr}" if thr is not None else ""
            msg = html.escape(c.get("message", "") or "")
            detail = c.get("detail")
            detail_html = ""
            if detail and st in ("critical", "warning", "error"):
                detail_str = json.dumps(detail, indent=2, ensure_ascii=False)
                detail_html = f'<pre class="detail">{html.escape(detail_str[:500])}</pre>'

            parts.append(f'''<tr>
              <td><span class="badge {cls}">{st}</span></td>
              <td><strong>{html.escape(c['check_name'])}</strong></td>
              <td>{msg}{detail_html}</td>
              <td>{val_str}</td>
              <td>{thr_str}</td>
            </tr>''')

        parts.append('</tbody></table></div>')

        # History timeline
        parts.append('<h2>Recent Reports</h2>')
        parts.append('<div class="card" style="overflow-x:auto;"><table><thead><tr><th>Time</th><th>OK</th><th>Warning</th><th>Critical</th><th>Error</th></tr></thead><tbody>')

        for h in history[:30]:
            r = h["report"]
            s2 = r.get("summary", {}) or {}
            time_str = self._render_time(h["reported_at"])
            report_link = f'/report/{html.escape(hostname)}/{html.escape(h["reported_at"])}'
            parts.append(f'''<tr style="cursor:pointer;" onclick="window.location='{report_link}'" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background=''">
              <td><a href="{report_link}" style="color:inherit;text-decoration:none;">{time_str}</a></td>
              <td>{_count_status(s2, "ok")}</td>
              <td><span style="color:{STATUS_COLOURS['warning']}">{_count_status(s2, "warning")}</span></td>
              <td><span style="color:{STATUS_COLOURS['critical']}">{_count_status(s2, "critical")}</span></td>
              <td><span style="color:{STATUS_COLOURS['error']}">{_count_status(s2, "error")}</span></td>
            </tr>''')

        parts.append('</tbody></table></div>')
        parts.append(PAGE_FOOT)
        self._send_html("".join(parts))

    def _handle_report(self, hostname: str, reported_at: str) -> None:
        report = get_report_by_timestamp(self.db_path, hostname, reported_at)
        if not report:
            self._send_html(PAGE_HEAD + f'<div class="empty"><h2>Not found</h2><p>Report not found</p></div>' + PAGE_FOOT, 404)
            return

        parts = [PAGE_HEAD]
        parts.append(f'<a class="back" href="/host/{html.escape(hostname)}">&larr; {html.escape(hostname)}</a>')
        parts.append(f'<h1>Report <small style="font-size:14px;color:#64748b;">{html.escape(self._render_time(reported_at))}</small></h1>')

        s = report.get("summary", {}) or {}
        parts.append(f'''<div class="card">
          <div class="stat-row">
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['critical']}">{_count_status(s, "critical")}</div><div class="lbl">Critical</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['warning']}">{_count_status(s, "warning")}</div><div class="lbl">Warning</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['error']}">{_count_status(s, "error")}</div><div class="lbl">Error</div></div>
            <div class="stat"><div class="num" style="color:{STATUS_COLOURS['ok']}">{_count_status(s, "ok")}</div><div class="lbl">OK</div></div>
          </div>
        </div>''')

        checks = report.get("checks", [])
        parts.append('<div class="card"><table><thead><tr><th>Status</th><th>Check</th><th>Message</th><th>Value</th><th>Threshold</th></tr></thead><tbody>')
        for c in sorted(checks, key=lambda x: ({"critical": 0, "error": 1, "warning": 2, "ok": 3}.get(x["status"], 9), x["check_name"])):
            st = c["status"]
            cls = self._status_class(st)
            val = c.get("metric_value")
            val_str = f'{val} {c.get("metric_unit","")}'.strip() if val is not None else ""
            thr = c.get("threshold")
            thr_str = f"{thr}" if thr is not None else ""
            msg = html.escape(c.get("message", "") or "")
            detail = c.get("detail")
            detail_html = ""
            if detail and st in ("critical", "warning", "error"):
                detail_html = f'<pre class="detail">{html.escape(json.dumps(detail, indent=2, ensure_ascii=False)[:500])}</pre>'
            parts.append(f'<tr><td><span class="badge {cls}">{st}</span></td><td><strong>{html.escape(c["check_name"])}</strong></td><td>{msg}{detail_html}</td><td>{val_str}</td><td>{thr_str}</td></tr>')
        parts.append('</tbody></table></div>')
        parts.append(PAGE_FOOT)
        self._send_html("".join(parts))


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fault Agent Web Dashboard")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT,
                        help=f"Listen port (default: {DEFAULT_PORT})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")

    WebHandler.db_path = args.db

    # Verify DB is accessible
    if not Path(args.db).exists():
        log.warning("database not found at %s — waiting for reports...", args.db)

    server = HTTPServer((args.bind, args.port), WebHandler)
    log.info("fault-agent-web v%s listening on %s:%s", VERSION, args.bind, args.port)
    log.info("dashboard: http://%s:%s/", args.bind, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down...")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()