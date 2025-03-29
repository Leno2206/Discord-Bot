import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, flash, session
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError
from requests_oauthlib import OAuth2Session
from datetime import datetime
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Ein Geheimschlüssel für Flask-Session
app.config['DISCORD_CLIENT_ID'] = '1351152498966265896'  # Deine Discord Client ID
app.config['DISCORD_CLIENT_SECRET'] = 'xoKG1lMT2Ljzzxngu3mIl7VEeWal9HoA'  # Dein Discord Client Secret
app.config['DISCORD_REDIRECT_URI'] = 'https://192.168.178.53/auth' 
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True
OAUTH2_CLIENT_ID = app.config['DISCORD_CLIENT_ID']
OAUTH2_CLIENT_SECRET = app.config['DISCORD_CLIENT_SECRET']
OAUTH2_REDIRECT_URI = app.config['DISCORD_REDIRECT_URI']

API_BASE_URL = 'https://discord.com/api'
AUTHORIZATION_BASE_URL = f"{API_BASE_URL}/oauth2/authorize"
TOKEN_URL = f"{API_BASE_URL}/oauth2/token"

def token_updater(token):
    session['oauth2_token'] = token

def make_session(token=None, state=None, scope=None):
    return OAuth2Session(
        client_id=OAUTH2_CLIENT_ID,
        token=token,
        state=state,
        scope=scope,
        redirect_uri=OAUTH2_REDIRECT_URI,
        auto_refresh_kwargs={
            'client_id': OAUTH2_CLIENT_ID,
            'client_secret': OAUTH2_CLIENT_SECRET,
        },
        auto_refresh_url=TOKEN_URL,
        token_updater=token_updater)
oauth = OAuth(app)
discord = oauth.register(
    name='discord',
    client_id=app.config['DISCORD_CLIENT_ID'],
    client_secret=app.config['DISCORD_CLIENT_SECRET'],
    authorize_url='https://discord.com/oauth2/authorize',
    authorize_params=None,
    access_token_url='https://discord.com/oauth2/token',
    refresh_token_url=None,
    client_kwargs={'scope': 'identify'},
)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Creates a connection to PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.DatabaseError as e:
        print(f"Error connecting to database: {e}")
        return None

@app.route("/auth")
def auth():
    authorization_response = request.url
    if authorization_response.startswith('http://'):
        authorization_response = authorization_response.replace('http://', 'https://')
    
    discord = make_session(state=request.args.get('state'))
    token = discord.fetch_token(
        TOKEN_URL,
        client_secret=OAUTH2_CLIENT_SECRET,
        authorization_response=authorization_response)
    session['oauth2_token'] = token

    # Fetch user data
    user = discord.get(f'{API_BASE_URL}/users/@me').json()
    session['discord_id'] = user['id']
    session['discord_user'] = user
    return redirect(url_for('index'))

@app.route("/login")
def login():
    session.permanent = True
    discord = make_session(scope="identify email")  # Define the required scopes here
    authorization_url, state = discord.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    print(f"OAuth State: {state}")  # Debugging line
    return redirect(authorization_url)

@app.route("/logout")
def logout():
    """Logout the user by clearing the session."""
    session.clear()  # Clear the entire session
    return redirect(url_for('index'))

@app.route('/')
def index():
    user = None
    notes = []
    status = False
    
    # Überprüfen, ob der Benutzer angemeldet ist
    if 'discord_id' in session:
        discord_id = session['discord_id']
        user = session.get('discord_user')
        
        # Verbindung zur Datenbank herstellen und Notizen abrufen
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, note FROM notes WHERE discord_id = %s", (discord_id,))
            notes = cursor.fetchall()
            conn.close()
            status = True
    
    # Render template mit Kontext-Daten
    return render_template('index.html', user=user, notes=notes, status=status)

@app.route("/add_note", methods=["POST"])
def add_note():
    """Adds a new note."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))  # If the user is not logged in, redirect to login page
    
    note_text = request.form["note"]
    discord_id = session['discord_id']  # Use the logged-in user's Discord ID
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))

    cursor = conn.cursor()
    cursor.execute("INSERT INTO notes (discord_id, note) VALUES (%s, %s)", (discord_id, note_text))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/edit_note/<int:note_id>", methods=["POST"])
def edit_note(note_id):
    """Edits an existing note."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))  # If the user is not logged in, redirect to login page
    
    new_text = request.form["note"]
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))

    cursor = conn.cursor()
    cursor.execute("UPDATE notes SET note = %s WHERE id = %s AND discord_id = %s", (new_text, note_id, session['discord_id']))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/delete_note/<int:note_id>")
def delete_note(note_id):
    """Deletes a note."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))  # If the user is not logged in, redirect to login page

    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))

    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE id = %s AND discord_id = %s", (note_id, session['discord_id']))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/add_reminder", methods=["POST"])
async def add_reminder():
    reminder_text = request.form["reminder"]
    reminder_time = request.form["reminder_time"]

    try:
        reminder_dt = datetime.strptime(reminder_time, "%Y-%m-%dT%H:%M")
    except ValueError:
        return "Ungültiges Zeitformat!", 400
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reminders (discord_id, note, reminder_time) VALUES ($1, $2, $3)",
                           "WEB_USER", reminder_text, reminder_dt)

    return redirect(url_for("index"))

@app.route("/delete_reminder/<int:reminder_id>")
async def delete_reminder(reminder_id):
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reminders WHERE id = $1", reminder_id)
    conn.commit()
    conn.close()

    return redirect(url_for("index"))

import subprocess

@app.route("/restart", methods=["POST"])
def restart_bot():
    """Neustart des Bots über Docker"""
    try:
        subprocess.run(["docker", "restart", "discord-bot"], check=True)  # Ändere den Containernamen falls nötig
        return {"success": True, "message": "Bot wird neu gestartet..."}
    except subprocess.CalledProcessError as e:
        return {"success": False, "message": f"Fehler beim Neustart: {str(e)}"}

@app.route("/status", methods=["GET"])
def get_bot_status():
    """Prüft, ob der Bot-Container läuft"""
    result = subprocess.run(["docker", "inspect", "--format='{{.State.Running}}'", "discord-bot"], capture_output=True, text=True)
    is_running = "true" in result.stdout
    return {"status": "online" if is_running else "offline"}
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=187, debug=True)