import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError
from requests_oauthlib import OAuth2Session
from datetime import datetime
import logging

import requests
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Ein Geheimschlüssel für Flask-Session
app.config['DISCORD_CLIENT_ID'] = '1351152498966265896'  # Deine Discord Client ID
app.config['DISCORD_CLIENT_SECRET'] = 'xoKG1lMT2Ljzzxngu3mIl7VEeWal9HoA'  # Dein Discord Client Secret
app.config['DISCORD_REDIRECT_URI'] = 'https://lenosnetzwerk.servebeer.com/auth' 
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True
OAUTH2_CLIENT_ID = app.config['DISCORD_CLIENT_ID']
OAUTH2_CLIENT_SECRET = app.config['DISCORD_CLIENT_SECRET']
OAUTH2_REDIRECT_URI = app.config['DISCORD_REDIRECT_URI']
logging.basicConfig(
    level=logging.INFO,  # Set the logging level to DEBUG to see all messages
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]  # Output logs to the console
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

# Taskboard Routen
@app.route('/tasks')
def tasks():
    """Zeigt das Taskboard mit allen Spalten und Aufgaben an."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("index"))
    
    cursor = conn.cursor()
    
    # Spalten abrufen
    cursor.execute("""
    SELECT id, name, position, is_backlog
    FROM taskboard_columns 
    WHERE discord_id = %s 
    ORDER BY position
""", (session['discord_id'],))
    
    columns = []
    for col_id, name, position, is_backlog in cursor.fetchall():
        # Aufgaben für jede Spalte abrufen
        cursor.execute("""
        SELECT id, title, description, is_recurring, is_completed, position
        FROM taskboard_tasks
        WHERE column_id = %s
        ORDER BY position
    """, (col_id,))
    
        tasks = [
            {
                'id': task_id,
                'title': title,
                'description': desc,
                'is_recurring': is_recurring,
                'is_completed': is_completed
            }
            for task_id, title, desc, is_recurring, is_completed, _ in cursor.fetchall()
        ]
            
        columns.append({
            'id': col_id,
            'name': name,
            'tasks': tasks,
            'is_backlog': is_backlog
        })
    
    conn.close()
    return render_template('tasks.html', columns=columns)

@app.route('/add_column', methods=['POST'])
def add_column():
    """Neue Spalte hinzufügen."""
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

    cursor = conn.cursor()

    # Höchste Position finden
    cursor.execute("""
        SELECT COALESCE(MAX(position), -1) 
        FROM taskboard_columns 
        WHERE discord_id = %s
    """, (session['discord_id'],))
    max_position = cursor.fetchone()[0]

    # Neue Spalte einfügen (inkl. is_backlog)
    cursor.execute("""
        INSERT INTO taskboard_columns (discord_id, name, position, is_backlog)
        VALUES (%s, %s, %s, %s)
    """, (session['discord_id'], column_name, max_position + 1, is_backlog))

    conn.commit()
    conn.close()

    return redirect(url_for('tasks'))

@app.route('/edit_column/<int:column_id>', methods=['POST'])
def edit_column(column_id):
    """Spalte bearbeiten."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    column_name = request.form.get('name')
    if not column_name:
        flash("Spaltenname darf nicht leer sein", "error")
        return redirect(url_for('tasks'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))
    
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE taskboard_columns 
        SET name = %s 
        WHERE id = %s AND discord_id = %s
    """, (column_name, column_id, session['discord_id']))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('tasks'))

@app.route('/delete_column/<int:column_id>', methods=['POST'])
def delete_column(column_id):
    """Spalte löschen."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))
    
    cursor = conn.cursor()
    # Zuerst alle zugehörigen Tasks löschen
    cursor.execute("""
        DELETE FROM taskboard_tasks 
        WHERE column_id = %s
    """, (column_id,))
    
    # Dann die Spalte löschen
    cursor.execute("""
        DELETE FROM taskboard_columns 
        WHERE id = %s AND discord_id = %s
    """, (column_id, session['discord_id']))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('tasks'))

@app.route('/add_task/<int:column_id>', methods=['POST'])
def add_task(column_id):
    """Aufgabe zu Spalte hinzufügen."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    title = request.form.get('title')
    description = request.form.get('description', '')
    
    if not title:
        flash("Aufgabentitel darf nicht leer sein", "error")
        return redirect(url_for('tasks'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))
    
    cursor = conn.cursor()
    
    # Prüfen, ob die Spalte dem Benutzer gehört
    cursor.execute("""
        SELECT id FROM taskboard_columns 
        WHERE id = %s AND discord_id = %s
    """, (column_id, session['discord_id']))
    
    if not cursor.fetchone():
        conn.close()
        flash("Keine Berechtigung für diese Aktion", "error")
        return redirect(url_for('tasks'))
    
    # Höchste Position in der Spalte finden
    cursor.execute("""
        SELECT COALESCE(MAX(position), -1) 
        FROM taskboard_tasks 
        WHERE column_id = %s
    """, (column_id,))
    
    max_position = cursor.fetchone()[0]
    
    # Neue Aufgabe einfügen
    cursor.execute("""
        INSERT INTO taskboard_tasks (column_id, title, description, position)
        VALUES (%s, %s, %s, %s)
    """, (column_id, title, description, max_position + 1))
    
    conn.commit()
    conn.close()
    return redirect(url_for('tasks'))

@app.route('/edit_task/<int:task_id>', methods=['POST'])
def edit_task(task_id):
    """Aufgabe bearbeiten."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    title = request.form.get('title')
    description = request.form.get('description', '')
    
    if not title:
        flash("Aufgabentitel darf nicht leer sein", "error")
        return redirect(url_for('tasks'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))
    
    cursor = conn.cursor()
    
    # Prüfen, ob die Aufgabe dem Benutzer gehört
    cursor.execute("""
        SELECT t.id
        FROM taskboard_tasks t
        JOIN taskboard_columns c ON t.column_id = c.id
        WHERE t.id = %s AND c.discord_id = %s
    """, (task_id, session['discord_id']))
    
    if not cursor.fetchone():
        conn.close()
        flash("Keine Berechtigung für diese Aktion", "error")
        return redirect(url_for('tasks'))
    
    # Aufgabe aktualisieren
    cursor.execute("""
        UPDATE taskboard_tasks
        SET title = %s, description = %s
        WHERE id = %s
    """, (title, description, task_id))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('tasks'))


@app.route("/sh4u")
def sh4u():
    return render_template("sh4u.html") 
@app.route('/delete_task/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    """Aufgabe löschen."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if not conn:
        flash("Fehler bei der Datenbankverbindung.", "error")
        return redirect(url_for("tasks"))
    
    cursor = conn.cursor()
    
    # Prüfen, ob die Aufgabe dem Benutzer gehört
    cursor.execute("""
        SELECT t.id
        FROM taskboard_tasks t
        JOIN taskboard_columns c ON t.column_id = c.id
        WHERE t.id = %s AND c.discord_id = %s
    """, (task_id, session['discord_id']))
    
    if not cursor.fetchone():
        conn.close()
        flash("Keine Berechtigung für diese Aktion", "error")
        return redirect(url_for('tasks'))
    
    # Aufgabe löschen
    cursor.execute("""
        DELETE FROM taskboard_tasks
        WHERE id = %s
    """, (task_id,))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('tasks'))

@app.route('/move_task', methods=['POST'])
def move_task():
    """Aufgabe per Drag & Drop verschieben."""
    if 'discord_id' not in session:
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401
    
    data = request.json
    task_id = data.get('task_id')
    new_column_id = data.get('column_id')
    
    if not task_id or not new_column_id:
        return jsonify({'success': False, 'message': 'Fehlende Daten'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Datenbankfehler'}), 500
    
    cursor = conn.cursor()
    
    # Prüfen, ob die Ziel-Spalte dem Benutzer gehört
    cursor.execute("""
        SELECT id FROM taskboard_columns 
        WHERE id = %s AND discord_id = %s
    """, (new_column_id, session['discord_id']))
    
    if not cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': 'Keine Berechtigung'}), 403
    
    # Aufgabe verschieben
    cursor.execute("""
        UPDATE taskboard_tasks
        SET column_id = %s
        WHERE id = %s
    """, (new_column_id, task_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/reorder_columns', methods=['POST'])
def reorder_columns():
    """Reihenfolge der Spalten aktualisieren."""
    if 'discord_id' not in session:
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401
    
    data = request.json
    column_order = data.get('column_order', [])
    
    if not column_order:
        return jsonify({'success': False, 'message': 'Fehlende Daten'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Datenbankfehler'}), 500
    
    cursor = conn.cursor()
    
    # Spalten-Positionen aktualisieren
    for index, column_id in enumerate(column_order):
        cursor.execute("""
            UPDATE taskboard_columns
            SET position = %s
            WHERE id = %s AND discord_id = %s
        """, (index, column_id, session['discord_id']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/')
def index():
    user = None
    notes = []
    reminders = []
    status = False
    allowed_members = []

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
            cursor.execute("SELECT id, note, reminder_time FROM reminders WHERE discord_id = %s", (user['id'],))
            reminders = cursor.fetchall()
            print(reminders)
            
            # Discord-Mitglieder abrufen vom Bot (docker-intern über Container-Name)
            res = requests.get("http://discord-bot:8000/api/discord/members")
            all_members = res.json()  # [{id: ..., name: ...}, ...]

            # Query: welche `target_user_id`s hat der aktuelle user berechtigt
            cursor.execute("""
                SELECT user_id FROM user_permissions
                WHERE target_user_id = %s AND permission_type = 'reminders'
            """, (discord_id,))
            
            allowed_ids = {row[0] for row in cursor.fetchall()}
            logging.info(allowed_ids)
            logging.info(all_members)
            # Nur die Mitglieder filtern, für die Berechtigung vorliegt
            allowed_members = [
                m for m in all_members
                if m["id"] in allowed_ids
            ]

            conn.close()
            status = True

    # Render template mit Kontext-Daten und den berechtigten Discord-Mitgliedern
    return render_template('index.html', user=user, notes=notes, status=status, reminders=reminders, allowed_members=allowed_members)

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

@app.route("/permissions")
def view_permissions():
    """View permissions granted and received."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
        
    granted_permissions = []
    received_permissions = []
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # Get permissions granted to others
        cursor.execute(
            "SELECT id, target_user_id, permission_type FROM user_permissions WHERE user_id = %s",
            (session['discord_id'],)
        )
        granted_permissions = cursor.fetchall()
        
        # Get permissions received from others
        cursor.execute(
            "SELECT id, user_id, permission_type FROM user_permissions WHERE target_user_id = %s",
            (session['discord_id'],)
        )
        received_permissions = cursor.fetchall()
        conn.close()
        
    return render_template(
        'permissions.html', 
        user=session.get('discord_user'),
        granted_permissions=granted_permissions,
        received_permissions=received_permissions
    )

@app.route("/add_reminder", methods=["POST"])
def add_reminder():
    logging.info("HIIIII")
    """Add a new reminder for yourself or another user."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))

    reminder_text = request.form.get("reminder")
    date = request.form.get("date")
    time = request.form.get("time")
    target_user_id = request.form.get("target_user_id")
    
    # Default to own user ID if no target specified
    if not target_user_id:
        target_user_id = session['discord_id']
        
    # If setting for another user, check permissions
    if target_user_id != session['discord_id']:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM user_permissions WHERE user_id = %s AND target_user_id = %s AND permission_type = %s",
                (target_user_id, session['discord_id'], "reminders")
            )
            permission = cursor.fetchone()
            conn.close()
            
            if not permission:
                flash("You don't have permission to set reminders for this user", "error")
                return redirect(url_for("index"))

    try:
        reminder_time = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        flash("Invalid date or time format", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (discord_id, note, reminder_time) VALUES (%s, %s, %s)",
            (target_user_id, reminder_text, reminder_time)
        )
        logging.info(reminder_text, reminder_time)
        conn.commit()
        conn.close()
        flash("Reminder added successfully", "success")
        
    return redirect(url_for("index"))

@app.route("/add_permission", methods=["POST"])
def add_permission():
    """Grant permission to another user to set reminders for you."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
        
    target_user_id = request.form.get("target_user_id")
    permission_type = request.form.get("permission_type", "reminders")
    
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_permissions (user_id, target_user_id, permission_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (session['discord_id'], target_user_id, permission_type)
            )
            conn.commit()
            flash("Permission granted successfully", "success")
        except Exception as e:
            flash(f"Error: {str(e)}", "error")
        finally:
            conn.close()
            
    return redirect(url_for("index"))

@app.route("/revoke_permission/<target_id>")
def revoke_permission(target_id):
    """Revoke permission from another user."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM user_permissions WHERE user_id = %s AND target_user_id = %s",
            (session['discord_id'], target_id)
        )
        conn.commit()
        conn.close()
        flash("Permission revoked", "success")
        
    return redirect(url_for("index"))
@app.route("/copy_task", methods=["POST"])
def copy_task():
    if 'discord_id' not in session:
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401

    data = request.get_json()
    original_task_id = data.get("task_id")
    target_column_id = data.get("column_id")

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Datenbankfehler'}), 500

    cursor = conn.cursor()
    # Original-Task laden
    cursor.execute("""
        SELECT t.title, t.description, t.is_recurring
        FROM taskboard_tasks t
        JOIN taskboard_columns c ON t.column_id = c.id
        WHERE t.id = %s AND c.discord_id = %s
    """, (original_task_id, session['discord_id']))
    task = cursor.fetchone()

    if not task:
        conn.close()
        return jsonify({'success': False, 'message': 'Aufgabe nicht gefunden oder keine Berechtigung'}), 404

    title, description, is_recurring = task

    # Neue Position
    cursor.execute("SELECT COALESCE(MAX(position), -1) FROM taskboard_tasks WHERE column_id = %s", (target_column_id,))
    max_position = cursor.fetchone()[0]

    # Kopieren
    cursor.execute("""
        INSERT INTO taskboard_tasks (column_id, title, description, is_recurring, position)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (target_column_id, title, description, is_recurring, max_position + 1))
    new_task_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'new_task_id': new_task_id})

@app.route("/toggle_recurring_task", methods=["POST"])
def toggle_recurring_task():
    if 'discord_id' not in session:
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401

    data = request.get_json()
    task_id = data.get("task_id")

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Datenbankfehler'}), 500

    cursor = conn.cursor()
    # Prüfen, ob Task dem User gehört
    cursor.execute("""
        SELECT t.is_recurring
        FROM taskboard_tasks t
        JOIN taskboard_columns c ON t.column_id = c.id
        WHERE t.id = %s AND c.discord_id = %s
    """, (task_id, session['discord_id']))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return jsonify({'success': False, 'message': 'Keine Berechtigung'}), 403

    current_state = result[0]
    new_state = not current_state

    cursor.execute("UPDATE taskboard_tasks SET is_recurring = %s WHERE id = %s", (new_state, task_id))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'is_completed': new_state})

@app.route("/delete_reminder/<int:reminder_id>")
def delete_reminder(reminder_id):
    """Löscht eine Erinnerung aus der Datenbank."""
    if 'discord_id' not in session:
        return redirect(url_for('login'))  # Stelle sicher, dass nur eingeloggte Nutzer Erinnerungen löschen können

    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", "error")
        return redirect(url_for("index"))

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = %s AND discord_id = %s", (reminder_id, session['discord_id']))
        conn.commit()
    except psycopg2.DatabaseError as e:
        flash(f"Database error: {e}", "error")
    finally:
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
@app.route('/toggle_completed_task', methods=['POST'])
def toggle_completed_task():
    if 'discord_id' not in session:
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401

    data = request.get_json()
    task_id = data.get("task_id")

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'message': 'Datenbankfehler'}), 500

    cursor = conn.cursor()
    # Prüfen, ob Task dem User gehört
    cursor.execute("""
        SELECT t.is_completed
        FROM taskboard_tasks t
        JOIN taskboard_columns c ON t.column_id = c.id
        WHERE t.id = %s AND c.discord_id = %s
    """, (task_id, session['discord_id']))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return jsonify({'success': False, 'message': 'Keine Berechtigung'}), 403

    current_state = result[0]
    new_state = not current_state

    cursor.execute("UPDATE taskboard_tasks SET is_completed = %s WHERE id = %s", (new_state, task_id))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'is_completed': new_state})
if __name__ == "__main__":
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        # Bestehende Tabellen
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            target_user_id TEXT NOT NULL,
            permission_type TEXT NOT NULL,
            UNIQUE(user_id, target_user_id, permission_type)
        )""")
        
        # Neue Tabellen für das Taskboard
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS taskboard_columns (
            id SERIAL PRIMARY KEY,
            discord_id TEXT NOT NULL,
            name TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS taskboard_tasks (
            id SERIAL PRIMARY KEY,
            column_id INTEGER REFERENCES taskboard_columns(id),
            title TEXT NOT NULL,
            description TEXT,
            position INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""
    ALTER TABLE taskboard_tasks 
    ADD COLUMN IF NOT EXISTS is_completed BOOLEAN DEFAULT FALSE
""")
        conn.commit()
        conn.close()
    
    app.run(host="0.0.0.0", port=187, debug=True)