from datetime import datetime, timedelta
import os
import sqlite3

import requests
from dotenv import load_dotenv
from flask import Flask, flash, g, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or "hc911-dev-secret-change-me"
app.config["DATABASE"] = os.getenv("DATABASE_URL", "hc911.db")
app.config["USERDB"] = os.getenv("USER_DATABASE_URL", "users.db")
app.config["INCIDENTS_API_URL"] = os.getenv(
    "INCIDENTS_API_URL", "https://hc911-proxy.onrender.com/api/proxy"
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

SEARCH_FIELDS = {
    "type": "type",
    "responder": "responder",
    "area": "area",
    "address": "address",
}


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = str(user_id)
        self.username = username


def ensure_users_table():
    db = get_db("USERDB")
    cursor = db.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            email TEXT UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()


def get_db(db_key: str):
    connection = getattr(g, db_key, None)
    if connection is None:
        connection = sqlite3.connect(app.config[db_key])
        connection.row_factory = sqlite3.Row
        setattr(g, db_key, connection)
    return connection


def fetch_user_by_id(user_id):
    db = get_db("USERDB")
    cursor = db.cursor()
    cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()


def fetch_user_by_username(username):
    db = get_db("USERDB")
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    return cursor.fetchone()


def get_today_bounds():
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start, today_start + timedelta(days=1)


def count_today_calls_by_type():
    db = get_db("DATABASE")
    cursor = db.cursor()
    today_start, today_end = get_today_bounds()

    cursor.execute(
        """
        SELECT type, address, time
        FROM events
        WHERE time >= ? AND time < ?
        ORDER BY type, address, time
        """,
        (
            today_start.strftime("%Y-%m-%d %H:%M:%S"),
            today_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    events = cursor.fetchall()

    grouped_counts = {}
    time_window = timedelta(minutes=5)

    for event in events:
        event_type = event["type"] or "Unknown"
        address = event["address"] or "Unknown address"
        event_time = datetime.strptime(event["time"], "%Y-%m-%d %H:%M:%S")
        key = (event_type, address)

        if key not in grouped_counts:
            grouped_counts[key] = {"count": 1, "last_time": event_time}
            continue

        if event_time - grouped_counts[key]["last_time"] > time_window:
            grouped_counts[key]["count"] += 1
            grouped_counts[key]["last_time"] = event_time

    summary = {}
    for (event_type, _), data in grouped_counts.items():
        summary[event_type] = summary.get(event_type, 0) + data["count"]

    return [
        {"type": event_type, "count": count}
        for event_type, count in sorted(summary.items(), key=lambda item: item[1], reverse=True)
    ]


def count_today_calls_with_locations():
    db = get_db("DATABASE")
    cursor = db.cursor()
    today_start, today_end = get_today_bounds()

    cursor.execute(
        """
        SELECT type, address, latitude, longitude, time
        FROM events
        WHERE time >= ? AND time < ?
        ORDER BY time DESC
        """,
        (
            today_start.strftime("%Y-%m-%d %H:%M:%S"),
            today_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    events = cursor.fetchall()

    return [
        {
            "type": event["type"] or "Unknown",
            "address": event["address"] or "Unknown address",
            "latitude": event["latitude"],
            "longitude": event["longitude"],
        }
        for event in events
        if event["latitude"] not in (None, 0) or event["longitude"] not in (None, 0)
    ]


def get_event_stats():
    db = get_db("DATABASE")
    cursor = db.cursor()

    cursor.execute("SELECT COUNT(*) AS total_events FROM events")
    total_events = cursor.fetchone()["total_events"]

    cursor.execute("SELECT MAX(time) AS latest_time FROM events")
    latest_time = cursor.fetchone()["latest_time"]

    today_start, today_end = get_today_bounds()
    cursor.execute(
        "SELECT COUNT(*) AS today_events FROM events WHERE time >= ? AND time < ?",
        (
            today_start.strftime("%Y-%m-%d %H:%M:%S"),
            today_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    today_events = cursor.fetchone()["today_events"]

    return {
        "total_events": total_events,
        "today_events": today_events,
        "latest_time": latest_time,
    }


def search_events(search_term, search_field, date_filter, limit=250):
    db = get_db("DATABASE")
    cursor = db.cursor()

    safe_field = SEARCH_FIELDS.get(search_field, "type")
    query = "SELECT * FROM events"
    conditions = []
    parameters = []

    if search_term:
        conditions.append(f"{safe_field} LIKE ?")
        parameters.append(f"%{search_term}%")

    if date_filter:
        date_start = datetime.strptime(date_filter, "%Y-%m-%d")
        date_end = date_start + timedelta(days=1)
        conditions.append("time >= ? AND time < ?")
        parameters.extend(
            [
                date_start.strftime("%Y-%m-%d %H:%M:%S"),
                date_end.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY time DESC LIMIT ?"
    parameters.append(limit)
    cursor.execute(query, parameters)
    return cursor.fetchall()


def fetch_active_incidents():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(app.config["INCIDENTS_API_URL"], headers=headers, timeout=20)
    response.raise_for_status()
    incidents = response.json()

    normalized = []
    for incident in incidents:
        latitude = incident.get("latitude")
        longitude = incident.get("longitude")
        normalized.append(
            {
                **incident,
                "latitude": float(latitude) if latitude not in (None, "") else None,
                "longitude": float(longitude) if longitude not in (None, "") else None,
            }
        )
    return normalized


@app.context_processor
def inject_app_context():
    stats = None
    if current_user.is_authenticated:
        try:
            stats = get_event_stats()
        except sqlite3.Error:
            stats = None
    return {"app_name": "HC911 Dashboard", "dashboard_stats": stats}


@login_manager.user_loader
def load_user(user_id):
    ensure_users_table()
    user = fetch_user_by_id(user_id)
    return User(user["id"], user["username"]) if user else None


@app.teardown_appcontext
def close_connection(_exception):
    for key in ("DATABASE", "USERDB"):
        db = getattr(g, key, None)
        if db is not None:
            db.close()


@app.route("/")
@login_required
def home():
    return redirect(url_for("daily_summary"))


@app.route("/search", methods=["GET", "POST"])
@login_required
def index():
    search_results = []
    search_term = ""
    search_field = "type"
    date_filter = ""

    if request.method == "POST":
        search_term = request.form.get("search_term", "").strip()
        search_field = request.form.get("search_field", "type")
        date_filter = request.form.get("date_filter", "").strip()
        search_results = search_events(search_term, search_field, date_filter)

    return render_template(
        "index.html",
        search_results=search_results,
        search_term=search_term,
        search_field=search_field,
        date_filter=date_filter,
        search_fields=SEARCH_FIELDS,
    )


@app.route("/daily_summary")
@login_required
def daily_summary():
    call_counts = count_today_calls_by_type()
    events = count_today_calls_with_locations()
    return render_template("daily_summary.html", call_counts=call_counts, events=events)


@app.route("/active_incidents")
@login_required
def active_incidents():
    try:
        incidents = fetch_active_incidents()
    except requests.RequestException as error:
        flash(f"Error fetching active incidents: {error}", "danger")
        incidents = []

    return render_template("active_incidents.html", incidents=incidents)


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_users_table()
    if current_user.is_authenticated:
        return redirect(url_for("daily_summary"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Enter both username and password.", "warning")
            return render_template("login.html")

        user = fetch_user_by_username(username)
        if user and check_password_hash(user["password"], password):
            login_user(User(user["id"], user["username"]), remember=True)
            flash("Logged in successfully!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("daily_summary"))

        flash("Invalid credentials. Please try again.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    ensure_users_table()
    if current_user.is_authenticated:
        return redirect(url_for("daily_summary"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("All fields are required.", "warning")
            return render_template("register.html")

        if len(password) < 8:
            flash("Use a password with at least 8 characters.", "warning")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "warning")
            return render_template("register.html")

        db = get_db("USERDB")
        cursor = db.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), email),
            )
            db.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash(
                "Username or email already exists. Please choose a different one.",
                "danger",
            )

    return render_template("register.html")


if __name__ == "__main__":
    app.run(debug=True)
