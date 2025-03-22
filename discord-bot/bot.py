import nextcord
import asyncpg
import os
import asyncio

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
        
        # Remove user_id column if it exists
        column_check = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_name = 'notes' AND column_name = 'user_id'
            )
        """)
        
        if column_check:
            await conn.execute("""
                ALTER TABLE notes DROP COLUMN user_id;
            """)



@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")
    await setup_database()

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

bot.run(TOKEN)