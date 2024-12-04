from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secret key for sessions

load_dotenv()

# use the .env file to create a DATABASE_URL should be just hc911.db 
DATABASE = os.getenv("DATABASE_URL")
USERDB = 'users.db'

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

def get_db(db_name):
    db = getattr(g, f'_{db_name}', None)
    if db is None:
        db = sqlite3.connect(db_name)
        db.row_factory = sqlite3.Row  # Allows fetching results as dictionaries
        setattr(g, f'_{db_name}', db)
    return db

# New function to count today's calls by type with time window and address grouping
def count_today_calls_by_type():
    db = get_db(DATABASE)
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
@login_required
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
    db = get_db(DATABASE)
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

# Fetch the user by ID for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    db = get_db(USERDB)
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    return User(id=user['id'], username=user['username']) if user else None

@app.route('/', methods=['GET', 'POST'])
@login_required
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

@app.route('/logout')
@login_required
def logout():
    logout_user()  # Logs the user out
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# Registration route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        
        # Hash the password for security
        password_hash = generate_password_hash(password)
        
        db = get_db(USERDB)
        cursor = db.cursor()
        
        try:
            # Insert the new user with hashed password and email
            cursor.execute("INSERT INTO users (username, password, email) VALUES (?, ?, ?)", 
                           (username, password_hash, email))
            db.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username or email already exists. Please choose a different one.", "danger")
        finally:
            db.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Process login form submission
        username = request.form['username']
        password = request.form['password']
        
        db = get_db(USERDB)
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        print(user['password'])
        
        if user and check_password_hash(user['password'], password):
            # Log in the user
            login_user(User(id=user['id'], username=user['username']))
            flash("Logged in successfully!", "success")
            
             # Redirect to the 'next' parameter if present, otherwise to 'daily_summary'
            next_page = request.args.get('next')
            return redirect(next_page or url_for('daily_summary'))
        else:
            # Invalid credentials
            flash("Invalid credentials. Please try again.", "danger")
            return redirect(url_for('login'))
    
    # If GET request, just render the login template
    return render_template('login.html')

if __name__ == '__main__':
    #change this if you want it to be remotely accessable by changing which line is commented out.
    #app.run(host='0.0.0.0', port=5000, debug=True)
    app.run(debug=True)
