import os
import io
import asyncio
import discord
import numpy as np
import matplotlib.pyplot as plt
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1554827174480056430"))
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.guilds = True
client = discord.Client(intents=intents)

def generate_test_chart() -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    x = np.linspace(0, 10, 100)
    y = np.sin(x) + np.random.normal(0, 0.1, 100)
    ax.plot(x, y, color='green', linewidth=2, label="Prix XAUUSD (Test)")
    ax.set_title("Test d'envoi du graphique TradeAgent Hub", fontsize=11)
    ax.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    png_bytes = buf.getvalue()
    plt.close(fig)
    return png_bytes

@client.event
async def on_ready():
    print(f"✅ Connecté à Discord en tant que : {client.user}")
    
    guild = client.get_guild(GUILD_ID)
    if guild:
        print(f"🏰 Serveur trouvé : {guild.name} (ID: {guild.id})")
    else:
        print(f"⚠️ Serveur ID {GUILD_ID} introuvable. Vérifiez que le Bot a rejoint le serveur.")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)
        print(f"📩 Salon trouvé : #{channel.name} ({CHANNEL_ID})")
    except Exception as e:
        print(f"❌ Impossible d'accéder au salon {CHANNEL_ID}. Erreur : {e}")
        await client.close()
        return

    embed = discord.Embed(
        title="🧪 TEST DE CONNEXION - TradeAgent Hub",
        description="Bot Discord connecté avec succès au serveur et au salon !",
        color=discord.Color.green()
    )
    embed.add_field(name="Serveur ID", value=str(GUILD_ID), inline=True)
    embed.add_field(name="Salon ID", value=str(CHANNEL_ID), inline=True)

    chart_bytes = generate_test_chart()
    file = discord.File(io.BytesIO(chart_bytes), filename="test_chart.png")
    embed.set_image(url="attachment://test_chart.png")

    await channel.send(file=file, embed=embed)
    print("🚀 Message et graphique envoyés avec succès !")
    await client.close()

if __name__ == "__main__":
    client.run(TOKEN)
