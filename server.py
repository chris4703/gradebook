import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from campus import course_model, detail_points, read_campus, rebase_models, replacement_estimates

WEB = Path(__file__).with_name("web")
DATA = Path(os.getenv("GRADEBOOK_DATA", "data"))
ZONE = ZoneInfo("America/Phoenix")
COOKIE = "gradebook_session"
STEPS = {"", "working", "submitted", "reviewed", "ask", "asked", "resolved"}
FLAGS = {"open", "missing", "submitted", "graded", "excused"}
ASSIGNMENT_COLUMNS = ["course", "title", "due", "status", "points", "possible", "source_id"]
LETTER = re.compile(r"^[A-D][+-]?$|^F$")


def today():
    return datetime.now(ZONE).date().isoformat()


def connect():
    return sqlite3.connect(DATA / "gradebook.db", timeout=10)


def initialize():
    os.umask(0o077)
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS progress (
                id TEXT PRIMARY KEY, step TEXT NOT NULL, note TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routine (day TEXT PRIMARY KEY, homework INTEGER NOT NULL);
        """)


def field(value, label, limit=200, required=True):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f"Enter {label} ({limit} characters maximum).")
    return value.strip()


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD.")
    return date.fromisoformat(value).isoformat()


def number(value, minimum=0, maximum=10000):
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("Scores and percentages must be numbers.") from None
    if isinstance(value, bool) or not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError("A score or percentage is out of range.")
    return result


def csv_rows(text, columns, extra=None):
    reader = csv.DictReader(io.StringIO(field(text, "CSV", 450_000), newline=""))
    if reader.fieldnames != columns and (extra is None or reader.fieldnames != [*columns, extra]):
        raise ValueError(f"Expected CSV columns: {', '.join(columns)}.")
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Check the number of columns on CSV line {reader.line_num}.")
        if any(value.strip() for value in row.values()):
            yield {key: value.strip() for key, value in row.items()}


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("Enter a gradebook snapshot.")
    grades, assignments = payload.get("grades"), payload.get("assignments")
    if not isinstance(grades, list) or not 1 <= len(grades) <= 50:
        raise ValueError("Enter between 1 and 50 class grades.")
    if not isinstance(assignments, list) or len(assignments) > 3000:
        raise ValueError("Enter up to 3,000 assignments.")
    source_date = iso_date(payload.get("asOf"))
    if source_date > today():
        raise ValueError("The snapshot date must be today or earlier.")
    clean = {"student": field(payload.get("student"), "the first name", 40),
             "term": field(payload.get("term"), "the term", 60),
             "asOf": source_date, "grades": [], "assignments": []}
    courses = set()
    for row in grades:
        if not isinstance(row, dict):
            raise ValueError("Check the grade rows.")
        course = field(row.get("course"), "the class", 100)
        letter = field(row.get("letter", ""), "the letter grade", 3, required=False).upper()
        if letter and not LETTER.fullmatch(letter):
            raise ValueError("Use an A–F letter grade or leave the letter blank if unavailable.")
        if course.casefold() in courses:
            raise ValueError(f"Duplicate class grade: {course}.")
        courses.add(course.casefold())
        clean["grades"].append(dict(course=course, letter=letter, percent=number(row.get("percent"))))
    identities = set()
    for row in assignments:
        if not isinstance(row, dict):
            raise ValueError("Check the assignment rows.")
        course = field(row.get("course"), "the class", 100)
        title = field(row.get("title"), "the assignment title")
        due = iso_date(row.get("due"))
        status = field(row.get("status") or "open", "the assignment status", 15).lower()
        if status not in FLAGS:
            raise ValueError(f"Unknown assignment status: {status}.")
        points, possible = number(row.get("points"), minimum=-10000), number(row.get("possible"))
        score_percent = number(row.get("score_percent"), minimum=-10000)
        if (points is not None and possible is None) or possible == 0:
            raise ValueError("Enter points possible above zero when providing earned points.")
        if (points is not None or score_percent is not None) and status == "open":
            status = "graded"
        source_id = field(row.get("source_id", ""), "the source ID", 100, required=False)
        identity = ["source", source_id] if source_id else [course, title, due]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        if key in identities:
            raise ValueError(f"Duplicate assignment: {title} ({course}, {due}). Add distinct source IDs if needed.")
        identities.add(key)
        clean["assignments"].append(dict(id=key, course=course, title=title, due=due, status=status,
                                         points=points, possible=possible, score_percent=score_percent, source_id=source_id))
    return clean


def replace_snapshot(payload):
    snapshot = normalize(payload)
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
        if previous:
            old = json.loads(previous[0])
            if old.get("student") == snapshot["student"] and old.get("term") == snapshot["term"]:
                prior = {row["id"]: row["possible"] for row in old["assignments"]
                         if row.get("points_verified")}
                for row in snapshot["assignments"]:
                    if row["id"] in prior and row["possible"] in {None, prior[row["id"]]}:
                        row.update(possible=prior[row["id"]], points_verified=True)
                snapshot["models"], snapshot["estimates"] = rebase_models(old, snapshot)
        db.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?)", (json.dumps(snapshot),))


def dashboard():
    day = today()
    with closing(connect()) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {}
        state["progress"] = {key: {"step": step, "note": note}
                             for key, step, note in db.execute("SELECT id, step, note FROM progress")}
        check = db.execute("SELECT homework FROM routine WHERE day=?", (day,)).fetchone()
    state.update(today=day, homework=bool(check and check[0]))
    return state


def signature(value):
    return hmac.new(SESSION_KEY, value.encode(), "sha256").hexdigest()


def session_token(role):
    value = f"{role}.{int(time.time()) + 14 * 86400}"
    return f"{value}.{signature(value)}"


class Handler(BaseHTTPRequestHandler):
    attempts = {}
    attempts_lock = Lock()

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def log_message(self, *_):
        pass

    def role(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            role, expires, mac = cookie[COOKIE].value.split(".")
            if (role in {"family", "parent"} and int(expires) > time.time()
                    and hmac.compare_digest(signature(f"{role}.{expires}"), mac)):
                return role
        except (ValueError, KeyError, TypeError):
            pass
        return None

    def respond(self, status, body, content_type="application/json; charset=utf-8", cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def json(self, result, status=200, cookie=None):
        self.respond(status, json.dumps(result).encode(), cookie=cookie)

    def do_GET(self):
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                  "/app.js": ("app.js", "text/javascript")}
        if path in assets:
            name, kind = assets[path]
            self.respond(200, (WEB / name).read_bytes(), f"{kind}; charset=utf-8")
        elif path == "/health":
            with closing(connect()) as db:
                db.execute("SELECT id FROM snapshot LIMIT 1").fetchone()
            self.json({"ok": True})
        elif path == "/api/dashboard":
            role = self.role()
            if role:
                self.json({"role": role, **dashboard()})
            else:
                self.json({"error": "Sign in to open the dashboard."}, 401)
        else:
            self.json({"error": "Page not found."}, 404)

    def enrich(self, path, payload):
        content = field(payload.get("text"), "the copied Campus page", 450_000)
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
            if not row:
                raise ValueError("Import a school snapshot first.")
            snapshot = json.loads(row[0])
            if path == "/api/course-breakdown":
                course = field(payload.get("course"), "a class", 100)
                model = course_model(content, snapshot, course, today())
                estimates = replacement_estimates(model, snapshot["assignments"])
                snapshot.setdefault("models", {})[course] = model
                snapshot.setdefault("estimates", {})[course] = estimates
                count = len(estimates)
            else:
                key = field(payload.get("id"), "an assignment", 24)
                assignment = next((item for item in snapshot["assignments"] if item["id"] == key), None)
                if assignment is None or assignment["points"] is not None:
                    raise ValueError("Choose an assignment without a recorded points score.")
                assignment.update(possible=detail_points(content, assignment), points_verified=True)
                count = 1
            db.execute("UPDATE snapshot SET payload=? WHERE id=1", (json.dumps(snapshot),))
        self.json({"count": count})

    def do_POST(self):
        path = urlsplit(self.path).path
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if self.headers.get("X-Gradebook") != "1" or (origin and origin not in {f"http://{host}", f"https://{host}"}):
            self.json({"error": "Open Gradebook directly to make changes."}, 403)
            return
        if self.headers.get_content_type() != "application/json":
            self.json({"error": "Expected JSON."}, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 1_000_000:
                self.json({"error": "Request is empty or too large."}, 413)
                return
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object.")
            if path == "/api/login":
                self.login(payload)
                return
            if path == "/api/logout":
                self.json({"ok": True}, cookie=f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
                return
            role = self.role()
            if not role:
                self.json({"error": "Sign in to make changes."}, 401)
                return
            if path in {"/api/import", "/api/campus-preview", "/api/course-breakdown", "/api/assignment-points"}:
                if role != "parent":
                    self.json({"error": "Use the parent access key to import."}, 403)
                    return
                if path in {"/api/course-breakdown", "/api/assignment-points"}:
                    self.enrich(path, payload)
                    return
                if path == "/api/campus-preview":
                    term, grades, assignments = read_campus(
                        field(payload.get("grades"), "copied Grades text", 200_000),
                        field(payload.get("assignments"), "copied Assignments text", 450_000),
                    )
                    self.json(normalize({"student": payload.get("student"), "term": term,
                                         "asOf": today(), "grades": grades, "assignments": assignments}))
                    return
                grades = list(csv_rows(payload.get("grades"), ["course", "letter", "percent"]))
                assignments = list(csv_rows(payload.get("assignments"), ASSIGNMENT_COLUMNS, "score_percent"))
                replace_snapshot({**payload, "grades": grades, "assignments": assignments})
            elif path == "/api/routine":
                if type(payload.get("homework")) is not bool:
                    raise ValueError("Select a homework check-in.")
                day = today()
                if payload.get("day") != day:
                    raise ValueError("The school day changed. Reload the dashboard and try again.")
                with closing(connect()) as db, db:
                    db.execute("INSERT OR REPLACE INTO routine VALUES (?, ?)", (day, payload["homework"]))
            elif path == "/api/progress":
                self.save_progress(payload)
            else:
                self.json({"error": "Page not found."}, 404)
                return
            self.json({"ok": True})
        except (ValueError, UnicodeError) as error:
            self.json({"error": str(error)}, 400)
        except sqlite3.Error:
            self.json({"error": "Gradebook storage is unavailable. Try again."}, 503)

    def save_progress(self, payload):
        key = field(payload.get("id"), "the assignment ID", 24)
        step = field(payload.get("step"), "a next step", 24, required=False)
        note = field(payload.get("note", ""), "a note", 500, required=False)
        if step not in STEPS:
            raise ValueError("Choose a listed next step.")
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
            if not row or not any(item["id"] == key for item in json.loads(row[0])["assignments"]):
                raise ValueError("This assignment has changed. Reload the dashboard and try again.")
            db.execute("INSERT OR REPLACE INTO progress VALUES (?, ?, ?)", (key, step, note))

    def login(self, payload):
        supplied = field(payload.get("key", ""), "your access key", 256, required=False).encode()
        now = time.monotonic()
        peer = self.client_address[0]
        with self.attempts_lock:
            expired = [key for key, (start, _) in self.attempts.items() if now - start >= 120]
            for key in expired:
                del self.attempts[key]
            start, count = self.attempts.get(peer, (now, 0))
            if count >= 12:
                self.json({"error": "Too many attempts. Try again in two minutes."}, 429)
                return
            self.attempts[peer] = (start, count + 1)
        parent = hmac.compare_digest(supplied, PARENT_KEY)
        family = hmac.compare_digest(supplied, FAMILY_KEY)
        if not (parent or family):
            self.json({"error": "That access key didn’t match."}, 401)
            return
        with self.attempts_lock:
            self.attempts.pop(peer, None)
        secure = "; Secure" if (self.headers.get("Origin") or "").startswith("https://") else ""
        value = session_token("parent" if parent else "family")
        self.json({"ok": True}, cookie=f"{COOKIE}={value}; Path=/; Max-Age=1209600; HttpOnly; SameSite=Strict{secure}")


if __name__ == "__main__":
    initialize()
    if sys.argv[1:] == ["import"]:
        replace_snapshot(json.load(sys.stdin))
        print("Snapshot imported.")
    elif not sys.argv[1:]:
        FAMILY_KEY = os.getenv("GRADEBOOK_FAMILY_KEY", "").encode()
        PARENT_KEY = os.getenv("GRADEBOOK_PARENT_KEY", "").encode()
        SESSION_KEY = os.getenv("GRADEBOOK_SESSION_SECRET", "").encode()
        if min(len(FAMILY_KEY), len(PARENT_KEY), len(SESSION_KEY)) < 16 or FAMILY_KEY == PARENT_KEY:
            sys.exit("Set distinct family and parent keys and a session secret, each at least 16 characters.")
        ThreadingHTTPServer(("", 8080), Handler).serve_forever()
    else:
        sys.exit("Usage: python server.py [import]")
