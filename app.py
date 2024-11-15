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

# New function to count today's calls by type with time window and address grouping
def count_today_calls_by_type():
    db = get_db()
    cursor = db.cursor()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    # Step 1: Fetch all today's calls by type and address within the date range
    query = """
    SELECT type, address, time
    FROM events
    WHERE time >= ? AND time < ?
    ORDER BY type, address, time
    """
    cursor.execute(query, (today_start.strftime("%Y-%m-%d %H:%M:%S"), today_end.strftime("%Y-%m-%d %H:%M:%S")))
    events = cursor.fetchall()
    
    # Step 2: Process and group calls by type, address, and within the specified time window (e.g., 5 minutes)
    grouped_counts = {}
    time_window = timedelta(minutes=5)  # Define the time window for grouping
    
    for event in events:
        event_type = event['type']
        address = event['address']
        time = datetime.strptime(event['time'], "%Y-%m-%d %H:%M:%S")
        
        # Generate a unique key for each (type, address) combination
        key = (event_type, address)
        
        if key not in grouped_counts:
            grouped_counts[key] = {'count': 1, 'last_time': time}
        else:
            # Check if the event is within the time window of the last recorded call at this address
            if time - grouped_counts[key]['last_time'] <= time_window:
                continue  # Skip this event as it's within the time window
            else:
                grouped_counts[key]['count'] += 1
                grouped_counts[key]['last_time'] = time  # Update last time for this type/address group
    
    # Step 3: Prepare a result list formatted for the template (grouped by type with aggregated counts)
    summary = {}
    for (event_type, _), data in grouped_counts.items():
        if event_type not in summary:
            summary[event_type] = data['count']
        else:
            summary[event_type] += data['count']
    
    # Convert summary into a sorted list for template
    sorted_summary = sorted(summary.items(), key=lambda x: x[1], reverse=True)
    return [{'type': type_, 'count': count} for type_, count in sorted_summary]

@app.route('/daily_summary')
def daily_summary():
    # Get the counts of today's calls by type, grouped by address and time window
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
    #change this if you want it to be remotely accessable by changing which line is commented out.
    #app.run(host='0.0.0.0', port=42069, debug=True)
    app.run(debug=True)
