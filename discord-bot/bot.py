import nextcord
import asyncpg
import os
import asyncio
import logging
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set the logging level to DEBUG to see all messages
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]  # Output logs to the console
)

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = nextcord.Client(intents=nextcord.Intents.default())

async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

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

@bot.event
async def on_ready():
    logging.info(f"Bot is online as {bot.user}")
    await setup_database()
    logging.info("Starting check_reminders task")
    bot.loop.create_task(check_reminders())  # Start the reminder checking loop

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
async def add_reminder(interaction: nextcord.Interaction, text: str, time: str):
    """
    Add a reminder for the user.
    :param text: The reminder text.
    :param time: The reminder time in ISO format (e.g., "2025-03-30T12:00").
    """
    try:
        reminder_time = datetime.fromisoformat(time).replace(tzinfo=timezone.utc)
    except ValueError:
        await interaction.response.send_message("Invalid time format! Use ISO format (e.g., 2025-03-30T12:00).", ephemeral=True)
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reminders (discord_id, note, reminder_time) VALUES ($1, $2, $3)",
            str(interaction.user.id), text, reminder_time
        )
    await interaction.response.send_message(f"Reminder set: {text} at {reminder_time} UTC ✅")

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
        # Get the current time as offset-naive to match the database
        now = datetime.now().replace(tzinfo=None)
        logging.debug(f"Current time: {now}")  # Debug: Print the current time

        async with db_pool.acquire() as conn:
            # Fetch reminders that are due
            rows = await conn.fetch(
                "SELECT id, discord_id, note FROM reminders WHERE reminder_time <= $1",
                now
            )
            logging.debug(f"Found {len(rows)} reminders due")  # Debug: Print the number of reminders found

            for row in rows:
                user_id = row["discord_id"]
                note = row["note"]
                reminder_id = row["id"]

                # Send the reminder to the user
                try:
                    user = await bot.fetch_user(int(user_id))
                    if user:
                        await user.send(f"⏰ Reminder: {note}")
                        logging.info(f"Sent reminder to user {user_id}: {note}")  # Info: Log success message
                except Exception as e:
                    logging.error(f"Failed to send reminder to {user_id}: {e}")  # Error: Log failure

                # Delete the reminder after sending it
                await conn.execute("DELETE FROM reminders WHERE id = $1", reminder_id)
                logging.debug(f"Deleted reminder {reminder_id}")  # Debug: Log deletion message

        await asyncio.sleep(60)  # Check every 60 seconds

bot.run(TOKEN)