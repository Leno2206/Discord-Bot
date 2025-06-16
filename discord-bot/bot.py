import nextcord
import asyncpg
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

app=FastAPI()

intents=nextcord.Intents.default()
intents.members=True
bot = nextcord.Client(intents=intents)
async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == API_KEY:
        return api_key_header
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN, detail="Invalid API key"
    )
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

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
async def setup_database():
    global db_pool
    db_pool = await create_db_pool()
    async with db_pool.acquire() as conn:
        # Create notes table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                discord_id TEXT NOT NULL,
                note TEXT NOT NULL
            )
        """)
        
        # Create reminders table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                discord_id TEXT NOT NULL,
                note TEXT NOT NULL,
                reminder_time TIMESTAMP NOT NULL
            )
        """)
        await conn.execute("""
    CREATE TABLE IF NOT EXISTS user_permissions (
        id SERIAL PRIMARY KEY,
        user_id TEXT NOT NULL,
        target_user_id TEXT NOT NULL,
        permission_type TEXT NOT NULL,
        UNIQUE(user_id, target_user_id, permission_type)
    )
""")

@bot.event
async def on_ready():
    logging.info(f"Bot is online as {bot.user}")
    await setup_database()
    logging.info("Starting check_reminders task")
    bot.loop.create_task(check_reminders())  # Start the reminder checking loop

@bot.slash_command(name="revoke_permission", description="Revoke permission from another user ❌")
async def revoke_permission(interaction: nextcord.Interaction, user_id: str, permission_type: str = "reminders"):
    """
    Revoke another user's permission to set reminders for you.
    
    :param user_id: The Discord ID of the user to revoke permission from.
    :param permission_type: The type of permission to revoke (default: reminders).
    """
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_permissions WHERE user_id = $1 AND target_user_id = $2 AND permission_type = $3",
            str(interaction.user.id), user_id, permission_type
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
        try:
            # Check if the permission already exists
            exists = await conn.fetchrow(
                "SELECT id FROM user_permissions WHERE user_id = $1 AND target_user_id = $2 AND permission_type = $3",
                str(interaction.user.id), user_id, permission_type
            )
            
            if exists:
                await interaction.response.send_message(f"User {user_id} already has permission to set {permission_type} for you.")
                return
                
            # Insert the new permission
            await conn.execute(
                "INSERT INTO user_permissions (user_id, target_user_id, permission_type) VALUES ($1, $2, $3)",
                str(interaction.user.id), user_id, permission_type
            )
            await interaction.response.send_message(f"Permission granted: User {user_id} can now set {permission_type} for you.")
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

@bot.slash_command(name="note", description="Save a note 💬")
async def note(interaction: nextcord.Interaction, text: str):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO notes (discord_id, note) VALUES ($1, $2)", 
                          str(interaction.user.id), text)
    await interaction.response.send_message(f"Note saved: {text} ✅")

@bot.slash_command(name="show_notes", description="Show your notes 📑")
async def show_notes(interaction: nextcord.Interaction):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT note FROM notes WHERE discord_id = $1", 
                               str(interaction.user.id))
    
    if rows:
        notes_str = "\n".join([row["note"] for row in rows])
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
            # Check if the user has permission
            permission = await conn.fetchrow(
                "SELECT id FROM user_permissions WHERE user_id = $1 AND target_user_id = $2 AND permission_type = $3",
                target_user_id, user_id, "reminders"
            )
            
            if not permission:
                await interaction.response.send_message(
                    f"You don't have permission to set reminders for user {target_user_id}.", 
                    ephemeral=True
                )
                return
                
            reminder_id = await conn.fetchval(
                "INSERT INTO reminders (discord_id, note, reminder_time) VALUES ($1, $2, $3) RETURNING id",
                target_user_id, text, reminder_time
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
            await conn.execute(
                "INSERT INTO reminders (discord_id, note, reminder_time) VALUES ($1, $2, $3)",
                user_id, text, reminder_time
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
        if direction == "granted":
            rows = await conn.fetch(
                "SELECT target_user_id, permission_type FROM user_permissions WHERE user_id = $1",
                user_id
            )
            
            if not rows:
                await interaction.response.send_message("You haven't granted permissions to anyone.")
                return
                
            permissions_list = "\n".join([f"User {row['target_user_id']} can set {row['permission_type']} for you" for row in rows])
            await interaction.response.send_message(f"Permissions you've granted:\n{permissions_list}")
        else:
            rows = await conn.fetch(
                "SELECT user_id, permission_type FROM user_permissions WHERE target_user_id = $1",
                user_id
            )
            
            if not rows:
                await interaction.response.send_message("You haven't received permissions from anyone.")
                return
                
            permissions_list = "\n".join([f"You can set {row['permission_type']} for user {row['user_id']}" for row in rows])

            await interaction.response.send_message(f"Permissions you've received:\n{permissions_list}")
    
@bot.slash_command(name="show_reminders", description="Show your reminders 📅")
async def show_reminders(interaction: nextcord.Interaction):
    """
    Show all reminders for the user.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT note, reminder_time FROM reminders WHERE discord_id = $1 ORDER BY reminder_time ASC",
            str(interaction.user.id)
        )
    
    if rows:
        reminders_str = "\n".join([f"{row['note']} - {row['reminder_time']}" for row in rows])
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
            # Fetch the next due reminder
            row = await conn.fetchrow(
                "SELECT id, discord_id, note, reminder_time FROM reminders WHERE reminder_time <= $1 ORDER BY reminder_time ASC LIMIT 1",
                now
            )

            if row:
                reminder_time = row["reminder_time"]
                time_until_reminder = (reminder_time - now).total_seconds()

                if time_until_reminder > 0:
                    logging.info(f"Sleeping for {time_until_reminder} seconds until the next reminder")
                    await asyncio.sleep(time_until_reminder)

                # Send the reminder
                user_id = row["discord_id"]
                note = row["note"]
                reminder_id = row["id"]

                try:
                    user = await bot.fetch_user(int(user_id))
                    if user:
                        await user.send(f"⏰ Reminder: {note}")
                        logging.info(f"Sent reminder to user {user_id}: {note}")
                except Exception as e:
                    logging.error(f"Failed to send reminder to {user_id}: {e}")

                # Delete the reminder after sending it
                await conn.execute("DELETE FROM reminders WHERE id = $1", reminder_id)
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