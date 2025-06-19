import nextcord
import aiomysql  # GEÄNDERT: asyncpg -> aiomysql
import os
import asyncio
import logging
from datetime import datetime, timezone
# Neue Importe für die API-Funktionalität
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN
import threading
from typing import List, Dict
from fastapi import Response

GUILD_ID = "1148225365400109148"
# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set the logging level to DEBUG to see all messages
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]  # Output logs to the console
)

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

API_KEY = os.getenv("API_KEY", "Leno2206")  # In Produktion ändern
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

app = FastAPI()

intents = nextcord.Intents.default()
intents.members = True
bot = nextcord.Client(intents=intents)

# Globale Variable für den Database Pool
db_pool = None

async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == API_KEY:
        return api_key_header
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN, detail="Invalid API key"
    )

# GEÄNDERT: MySQL Connection Pool mit Retry-Mechanismus
async def create_db_pool():
    max_retries = 5
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            return await aiomysql.create_pool(
                host='mysql_wp',  # Docker service name
                port=3306,
                user='wpuser',
                password='wppassword',
                db='mysql_wp',
                charset='utf8mb4',
                autocommit=True,
                maxsize=10
            )
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} failed to connect to database: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                raise Exception(f"Failed to connect to database after {max_retries} attempts")

@app.get('/api/discord/members')
async def get_discord_members():
    guild = bot.get_guild(int(GUILD_ID))
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found")
    if not guild.chunked:
        await guild.chunk(cache=True)
    members = [
        {"id": str(m.id), "name": m.display_name}
        for m in guild.members if not m.bot
    ]
    
    return members

# GEÄNDERT: MySQL Table Setup mit Cursor
async def setup_database():
    global db_pool
    db_pool = await create_db_pool()
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # GEÄNDERT: SERIAL -> AUTO_INCREMENT, TEXT -> VARCHAR/TEXT
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_id VARCHAR(255) NOT NULL,
                    note TEXT NOT NULL,
                    INDEX idx_discord_id (discord_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            # GEÄNDERT: TIMESTAMP -> DATETIME
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_id VARCHAR(255) NOT NULL,
                    note TEXT NOT NULL,
                    reminder_time DATETIME NOT NULL,
                    INDEX idx_discord_id (discord_id),
                    INDEX idx_reminder_time (reminder_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_permissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    target_user_id VARCHAR(255) NOT NULL,
                    permission_type VARCHAR(255) NOT NULL,
                    UNIQUE KEY unique_permission (user_id, target_user_id, permission_type),
                    INDEX idx_user_id (user_id),
                    INDEX idx_target_user_id (target_user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

@bot.event
async def on_ready():
    global db_pool
    logging.info(f"Bot logged in as {bot.user}")
    
    # Datenbank-Setup mit Retry-Mechanismus
    try:
        if 'db_pool' not in globals() or db_pool is None:
            await setup_database()
        logging.info("Database setup completed successfully")
        logging.info(f"Bot is online as {bot.user}")
        logging.info("Starting check_reminders task")
        bot.loop.create_task(check_reminders())  # Start the reminder checking loop
    except Exception as e:
        logging.error(f"Failed to initialize database: {e}")
        # Bot wird nicht beendet, um Restart-Loop zu verhindern
        logging.info("Bot will continue running without database functionality")

# GEÄNDERT: Alle $1, $2, $3 -> %s, %s, %s
@bot.slash_command(name="revoke_permission", description="Revoke permission from another user ❌")
async def revoke_permission(interaction: nextcord.Interaction, user_id: str, permission_type: str = "reminders"):
    """
    Revoke another user's permission to set reminders for you.
    
    :param user_id: The Discord ID of the user to revoke permission from.
    :param permission_type: The type of permission to revoke (default: reminders).
    """
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM user_permissions WHERE user_id = %s AND target_user_id = %s AND permission_type = %s",
                (str(interaction.user.id), user_id, permission_type)
            )
            await interaction.response.send_message(f"Permission revoked: User {user_id} can no longer set {permission_type} for you.")

@bot.slash_command(name="grant_permission", description="Grant permission to another user ✅")
async def grant_permission(interaction: nextcord.Interaction, user_id: str, permission_type: str = "reminders"):
    """
    Grant another user permission to set reminders for you.
    
    :param user_id: The Discord ID of the user to grant permission to.
    :param permission_type: The type of permission to grant (default: reminders).
    """
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            try:
                # Check if the permission already exists
                await cursor.execute(
                    "SELECT id FROM user_permissions WHERE user_id = %s AND target_user_id = %s AND permission_type = %s",
                    (str(interaction.user.id), user_id, permission_type)
                )
                exists = await cursor.fetchone()
                
                if exists:
                    await interaction.response.send_message(f"User {user_id} already has permission to set {permission_type} for you.")
                    return
                    
                # Insert the new permission
                await cursor.execute(
                    "INSERT INTO user_permissions (user_id, target_user_id, permission_type) VALUES (%s, %s, %s)",
                    (str(interaction.user.id), user_id, permission_type)
                )
                await interaction.response.send_message(f"Permission granted: User {user_id} can now set {permission_type} for you.")
            except Exception as e:
                await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

@bot.slash_command(name="note", description="Save a note 💬")
async def note(interaction: nextcord.Interaction, text: str):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("INSERT INTO notes (discord_id, note) VALUES (%s, %s)", 
                              (str(interaction.user.id), text))
    await interaction.response.send_message(f"Note saved: {text} ✅")

@bot.slash_command(name="show_notes", description="Show your notes 📑")
async def show_notes(interaction: nextcord.Interaction):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT note FROM notes WHERE discord_id = %s", 
                               (str(interaction.user.id),))
            rows = await cursor.fetchall()
    
    if rows:
        notes_str = "\n".join([row[0] for row in rows])  # GEÄNDERT: row[0] statt row["note"]
        await interaction.response.send_message(f"Your notes:\n{notes_str}")
    else:
        await interaction.response.send_message("You have no saved notes. 😔")

@bot.slash_command(name="add_reminder", description="Add a reminder ⏰")
async def add_reminder(interaction: nextcord.Interaction, text: str, time: str, target_user_id: str = None):
    """
    Add a reminder for yourself or another user.
    
    :param text: The reminder text.
    :param time: The reminder time in ISO format (e.g., "2025-03-30T12:00").
    :param target_user_id: Optional Discord ID of the user to set the reminder for.
    """
    try:
        reminder_time = datetime.fromisoformat(time)
        if reminder_time.tzinfo is not None:
            reminder_time = reminder_time.replace(tzinfo=None)
    except ValueError:
        await interaction.response.send_message(
            "Invalid time format! Use ISO format (e.g., 2025-03-30T12:00).", ephemeral=True
        )
        return
        
    user_id = str(interaction.user.id)
    
    # If target_user_id is provided, check permissions
    if target_user_id and target_user_id != user_id:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # Check if the user has permission
                await cursor.execute(
                    "SELECT id FROM user_permissions WHERE user_id = %s AND target_user_id = %s AND permission_type = %s",
                    (target_user_id, user_id, "reminders")
                )
                permission = await cursor.fetchone()
                
                if not permission:
                    await interaction.response.send_message(
                        f"You don't have permission to set reminders for user {target_user_id}.", 
                        ephemeral=True
                    )
                    return
                    
                await cursor.execute(
                    "INSERT INTO reminders (discord_id, note, reminder_time) VALUES (%s, %s, %s)",
                    (target_user_id, text, reminder_time)
                )
                
                await interaction.response.send_message(
                    f"Reminder set for user {target_user_id}: {text} at {reminder_time} UTC ✅"
                )
                
                # Try to notify the target user
                try:
                    target_user = await bot.fetch_user(int(target_user_id))
                    if target_user:
                        await target_user.send(
                            f"User {interaction.user.name} ({user_id}) set a reminder for you: {text} at {reminder_time} UTC"
                        )
                except Exception as e:
                    logging.error(f"Failed to notify user {target_user_id}: {e}")
    else:
        # Set reminder for self (original functionality)
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO reminders (discord_id, note, reminder_time) VALUES (%s, %s, %s)",
                    (user_id, text, reminder_time)
                )
        await interaction.response.send_message(f"Reminder set: {text} at {reminder_time} UTC ✅")

@bot.slash_command(name="list_permissions", description="List all permissions ⚙️")
async def list_permissions(interaction: nextcord.Interaction, direction: str = "granted"):
    """
    List all permissions you've granted to others or others have granted to you.
    
    :param direction: Either 'granted' (permissions you've given) or 'received' (permissions you've received).
    """
    user_id = str(interaction.user.id)
    
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            if direction == "granted":
                await cursor.execute(
                    "SELECT target_user_id, permission_type FROM user_permissions WHERE user_id = %s",
                    (user_id,)
                )
                rows = await cursor.fetchall()
                
                if not rows:
                    await interaction.response.send_message("You haven't granted permissions to anyone.")
                    return
                    
                permissions_list = "\n".join([f"User {row[0]} can set {row[1]} for you" for row in rows])
                await interaction.response.send_message(f"Permissions you've granted:\n{permissions_list}")
            else:
                await cursor.execute(
                    "SELECT user_id, permission_type FROM user_permissions WHERE target_user_id = %s",
                    (user_id,)
                )
                rows = await cursor.fetchall()
                
                if not rows:
                    await interaction.response.send_message("You haven't received permissions from anyone.")
                    return
                    
                permissions_list = "\n".join([f"You can set {row[1]} for user {row[0]}" for row in rows])
                await interaction.response.send_message(f"Permissions you've received:\n{permissions_list}")
    
@bot.slash_command(name="show_reminders", description="Show your reminders 📅")
async def show_reminders(interaction: nextcord.Interaction):
    """
    Show all reminders for the user.
    """
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT note, reminder_time FROM reminders WHERE discord_id = %s ORDER BY reminder_time ASC",
                (str(interaction.user.id),)
            )
            rows = await cursor.fetchall()
    
    if rows:
        reminders_str = "\n".join([f"{row[0]} - {row[1]}" for row in rows])
        await interaction.response.send_message(f"Your reminders:\n{reminders_str}")
    else:
        await interaction.response.send_message("You have no reminders. 😔")

async def check_reminders():
    """
    Periodically check the database for reminders that are due and send them to users.
    """
    logging.info("check_reminders function started")
    while True:
        now = datetime.now().replace(tzinfo=None)
        logging.info(f"Current time: {now}")

        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # Fetch the next due reminder
                await cursor.execute(
                    "SELECT id, discord_id, note, reminder_time FROM reminders WHERE reminder_time <= %s ORDER BY reminder_time ASC LIMIT 1",
                    (now,)
                )
                row = await cursor.fetchone()

                if row:
                    reminder_id, user_id, note, reminder_time = row
                    time_until_reminder = (reminder_time - now).total_seconds()

                    if time_until_reminder > 0:
                        logging.info(f"Sleeping for {time_until_reminder} seconds until the next reminder")
                        await asyncio.sleep(time_until_reminder)

                    # Send the reminder
                    try:
                        user = await bot.fetch_user(int(user_id))
                        if user:
                            await user.send(f"⏰ Reminder: {note}")
                            logging.info(f"Sent reminder to user {user_id}: {note}")
                    except Exception as e:
                        logging.error(f"Failed to send reminder to {user_id}: {e}")

                    # Delete the reminder after sending it
                    await cursor.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
                    logging.info(f"Deleted reminder {reminder_id}")
                else:
                    # No reminders found, sleep for a short duration before checking again
                    logging.info("No reminders found, sleeping for 10 seconds")
                    await asyncio.sleep(10)

def run_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Die main-Funktion muss angepasst werden, um auch die API zu starten
def main():
    # FastAPI in einem separaten Thread starten
    api_thread = threading.Thread(target=run_api)
    api_thread.daemon = True
    api_thread.start()
    logging.info("API server started on port 8000")
    # Discord Bot starten
    bot.run(TOKEN)

if __name__ == "__main__":
    main()