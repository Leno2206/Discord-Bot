import os
import pymysql  # Ersetzt psycopg2
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError
from requests_oauthlib import OAuth2Session
from datetime import datetime
import logging
import requests
import subprocess

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['DISCORD_CLIENT_ID'] = '1351152498966265896'
app.config['DISCORD_CLIENT_SECRET'] = 'xoKG1lMT2Ljzzxngu3mIl7VEeWal9HoA'
app.config['DISCORD_REDIRECT_URI'] = 'https://discord.yusuf-kahya.com/auth' 
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True

OAUTH2_CLIENT_ID = app.config['DISCORD_CLIENT_ID']
OAUTH2_CLIENT_SECRET = app.config['DISCORD_CLIENT_SECRET']
OAUTH2_REDIRECT_URI = app.config['DISCORD_REDIRECT_URI']

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

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

# MySQL Verbindungskonfiguration mit Retry-Mechanismus
def get_db_connection():
    """Erstellt eine Verbindung zur MySQL-Datenbank mit Retry-Mechanismus."""
    max_retries = 5
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            conn = pymysql.connect(
                host=os.getenv("MYSQL_HOST", "mysql_wp"),
                user=os.getenv("MYSQL_USER", "wpuser"),
                password=os.getenv("MYSQL_PASSWORD", "wppassword"),
                db=os.getenv("MYSQL_DATABASE", "mysql_wp"),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            return conn
        except pymysql.Error as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} failed to connect to database: {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(retry_delay)
            else:
                logging.error("Failed to initialize database")
                return None
@app.route("/restart", methods=["POST"])
def restart_bot():
    """Neustart des Bots über Docker"""
    try:
        subprocess.run(["docker", "restart", "discord-bot"], check=True)  # Ändere den Containernamen falls nötig
        return {"success": True, "message": "Bot wurde neu gestartet."}
    except subprocess.CalledProcessError as e:
        return {"success": False, "message": f"Fehler beim Neustart: {str(e)}"}
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

    user = discord.get(f'{API_BASE_URL}/users/@me').json()
    session['discord_id'] = user['id']
    session['discord_user'] = user
    return redirect(url_for('index'))

@app.route("/add_note", methods=["POST"])
def add_note():
    """Fügt eine neue Notiz hinzu."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))

    note_text = request.form.get("note")
    discord_id = session['discord_id']

    conn = get_db_connection()
    if not conn:
        flash("Fehler beim Verbinden mit der Datenbank.", "error")
        return redirect(url_for("index"))

    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO notes (discord_id, note) VALUES (%s, %s)", (discord_id, note_text))
        conn.commit()
    except pymysql.MySQLError as e:
        flash("Fehler beim Einfügen der Notiz.", "error")
        print(f"MySQL-Fehler: {e}")
    finally:
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

    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM notes WHERE id = %s AND discord_id = %s", 
                         (note_id, session['discord_id']))
            conn.commit()
        flash("Note deleted successfully", "success")
    except pymysql.Error as e:
        flash(f"Database error: {str(e)}", "error")
        conn.rollback()
    finally:
        conn.close()
    
    return redirect(url_for("index"))
@app.route("/login")
def login():
    session.permanent = True
    discord = make_session(scope="identify email")
    authorization_url, state = discord.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('index'))

# Taskboard Routen (angepasst für MySQL)
@app.route('/tasks')
def tasks():
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("index"))
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, position, is_backlog
                FROM taskboard_columns 
                WHERE discord_id = %s 
                ORDER BY position
            """, (session['discord_id'],))
            
            columns = []
            for col in cursor.fetchall():
                cursor.execute("""
                    SELECT id, title, description, is_recurring, is_completed, position
                    FROM taskboard_tasks
                    WHERE column_id = %s
                    ORDER BY position
                """, (col['id'],))
                
                tasks = cursor.fetchall()
                columns.append({
                    'id': col['id'],
                    'name': col['name'],
                    'tasks': tasks,
                    'is_backlog': col['is_backlog']
                })
            
        return render_template('tasks.html', columns=columns)
    except pymysql.Error as e:
        logging.error(f"Database error: {e}")
        flash("Datenbankfehler", "error")
        return redirect(url_for("index"))
    finally:
        conn.close()

# Weitere Routen müssen ähnlich angepasst werden...
# Hier ein Beispiel für die add_column Route:

@app.route('/add_column', methods=['POST'])
def add_column():
    if 'discord_id' not in session:
        return redirect(url_for('login'))

    column_name = request.form.get('name')
    is_backlog = bool(request.form.get('is_backlog'))

    if not column_name:
        flash("Spaltenname darf nicht leer sein", "error")
        return redirect(url_for('tasks'))

    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(MAX(position), -1) 
                FROM taskboard_columns 
                WHERE discord_id = %s
            """, (session['discord_id'],))
            max_position = cursor.fetchone()['COALESCE(MAX(position), -1)']

            cursor.execute("""
                INSERT INTO taskboard_columns (discord_id, name, position, is_backlog)
                VALUES (%s, %s, %s, %s)
            """, (session['discord_id'], column_name, max_position + 1, is_backlog))
            
            conn.commit()
            return redirect(url_for('tasks'))
    except pymysql.Error as e:
        logging.error(f"Database error: {e}")
        flash("Datenbankfehler", "error")
        return redirect(url_for("tasks"))
    finally:
        conn.close()

# Alle anderen Routen müssen ähnlich angepasst werden...
@app.route("/status", methods=["GET"])
def get_bot_status():
    """Prüft, ob der Bot-Container läuft"""
    result = subprocess.run(["docker", "inspect", "--format='{{.State.Running}}'", "discord-bot"], capture_output=True, text=True)
    is_running = "true" in result.stdout
    return {"status": "online" if is_running else "offline"}
@app.route('/')
def index():
    user = None
    notes = []
    reminders = []
    status = False
    allowed_members = []

    if 'discord_id' in session:
        discord_id = session['discord_id']
        user = session.get('discord_user')
        
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, note FROM notes WHERE discord_id = %s", (discord_id,))
                    notes = cursor.fetchall()
                    
                    cursor.execute("SELECT id, note, reminder_time FROM reminders WHERE discord_id = %s", (user['id'],))
                    reminders = cursor.fetchall()
                    
                    # Discord-Mitglieder abrufen
                    res = requests.get("http://discord-bot:8000/api/discord/members")
                    all_members = res.json()

                    cursor.execute("""
                        SELECT user_id FROM user_permissions
                        WHERE target_user_id = %s AND permission_type = 'reminders'
                    """, (discord_id,))
                    
                    allowed_ids = {row['user_id'] for row in cursor.fetchall()}
                    allowed_members = [m for m in all_members if m["id"] in allowed_ids]
                    
                    status = True
            except pymysql.Error as e:
                logging.error(f"Database error: {e}")
            finally:
                conn.close()

    return render_template('index.html', 
                         user=user, 
                         notes=notes, 
                         status=status, 
                         reminders=reminders, 
                         allowed_members=allowed_members)

# Initialisierung der Datenbanktabellen
def init_db():
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cursor:
            # Tabellen erstellen
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_permissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    target_user_id VARCHAR(255) NOT NULL,
                    permission_type VARCHAR(255) NOT NULL,
                    UNIQUE KEY unique_permission (user_id, target_user_id, permission_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS taskboard_columns (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_id VARCHAR(255) NOT NULL,
                    name TEXT NOT NULL,
                    position INT DEFAULT 0,
                    is_backlog BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS taskboard_tasks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    column_id INT,
                    title TEXT NOT NULL,
                    description TEXT,
                    position INT DEFAULT 0,
                    is_recurring BOOLEAN DEFAULT FALSE,
                    is_completed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (column_id) REFERENCES taskboard_columns(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_id VARCHAR(255) NOT NULL,
                    note TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_discord_id (discord_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_id VARCHAR(255) NOT NULL,
                    note TEXT NOT NULL,
                    reminder_time DATETIME NOT NULL,
                    INDEX idx_discord_id (discord_id),
                    INDEX idx_reminder_time (reminder_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            conn.commit()
            return True
    except pymysql.Error as e:
        logging.error(f"Database initialization error: {e}")
        return False
    finally:
        conn.close()

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

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE notes SET note = %s WHERE id = %s AND discord_id = %s",
                (new_text, note_id, session['discord_id'])
            )
        conn.commit()
    except pymysql.Error as e:
        flash(f"Database error: {str(e)}", "error")
        conn.rollback()
    finally:
        conn.close()
    return redirect(url_for("index"))

if __name__ == "__main__":
    if init_db():
        app.run(host="0.0.0.0", port=187, debug=True)
    else:
        logging.error("Failed to initialize database")