from flask import Flask, render_template, request, redirect, url_for, session, g
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secret key for sessions

load_dotenv()

# use the .env file to create a DATABASE_URL should be just hc911.db 
DATABASE = os.getenv("DATABASE_URL")


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row  # Allows fetching results as dictionaries
    return db

# New function to count today's calls by type
def count_today_calls_by_type():
    db = get_db()
    cursor = db.cursor()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    # SQL query to count the types of calls for today
    query = """
    SELECT type, COUNT(*) as count
    FROM events
    WHERE time >= ? AND time < ?
    GROUP BY type
    ORDER BY count DESC
    """
    cursor.execute(query, (today_start.strftime("%Y-%m-%d %H:%M:%S"), today_end.strftime("%Y-%m-%d %H:%M:%S")))
    return cursor.fetchall()

@app.route('/daily_summary')
def daily_summary():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Get the counts of today's calls by type
    call_counts = count_today_calls_by_type()
    return render_template('daily_summary.html', call_counts=call_counts)

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def search_events(search_term, search_field, date_filter):
    db = get_db()
    cursor = db.cursor()
    
    # Base query to select all records
    query = "SELECT * FROM events"
    parameters = []

    # Dynamically build conditions based on inputs
    conditions = []
    
    if search_term:
        conditions.append(f"{search_field} LIKE ?")
        parameters.append(f"%{search_term}%")
    
    if date_filter:
        # Parse the date to create a range for the entire day
        date_start = datetime.strptime(date_filter, "%Y-%m-%d")
        date_end = date_start + timedelta(days=1)
        conditions.append("time >= ? AND time < ?")
        parameters.extend([date_start.strftime("%Y-%m-%d %H:%M:%S"), date_end.strftime("%Y-%m-%d %H:%M:%S")])
    
    # Add conditions to the query if any are specified
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    # Order by time descending to show the newest records first
    query += " ORDER BY time DESC"
    
    # Execute the query with parameters
    cursor.execute(query, parameters)
    return cursor.fetchall()


@app.route('/', methods=['GET', 'POST'])
def index():
    
    search_results = []
    search_term = ''
    search_field = 'type'  # Default search field
    date_filter = None
    if request.method == 'POST':
        search_term = request.form.get('search_term', '')
        search_field = request.form.get('search_field', 'type')
        date_filter = request.form.get('date_filter', '')
        search_results = search_events(search_term, search_field, date_filter)
    return render_template('index.html', search_results=search_results, search_term=search_term, search_field=search_field, date_filter=date_filter)

if __name__ == '__main__':
    #app.run(host='0.0.0.0', port=42069, debug=True)
    app.run(debug=True)
