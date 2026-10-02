import os
import io
import asyncio
import discord
from dotenv import load_dotenv

load_dotenv()

class DiscordTradeBot:
    def __init__(self, mt5_handler=None):
        self.mt5_handler = mt5_handler
        self.token = os.getenv("DISCORD_BOT_TOKEN")
        self.channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        
        intents = discord.Intents.default()
        intents.guilds = True
        self.client = discord.Client(intents=intents)

    async def send_plan_notification(self, plan: dict, image_bytes: bytes = None):
        """Envoie une alerte de plan de trading enrichie avec le graphique sur Discord."""
        if not self.channel_id:
            print("⚠️ DISCORD_CHANNEL_ID non configuré.")
            return

        try:
            channel = await self.client.fetch_channel(self.channel_id)
            
            bias = plan.get("bias", "NEUTRAL")
            bias_color = discord.Color.green() if bias == "BULLISH" else (
                discord.Color.red() if bias == "BEARISH" else discord.Color.gold()
            )

            embed = discord.Embed(
                title=f"🚨 NOUVEAU PLAN DE TRADING : {plan.get('symbol', 'XAUUSD')}",
                description=f"**Biais Analytique :** `{bias}`\n**Confiance Engine :** `{plan.get('confidence', 0)}%`",
                color=bias_color
            )
            embed.add_field(name="Entry Range", value=f"`{plan.get('entry_low', 0)} - {plan.get('entry_high', 0)}`", inline=True)
            embed.add_field(name="Stop Loss", value=f"`{plan.get('sl', 0)}`", inline=True)
            embed.add_field(name="Take Profit", value=f"`{plan.get('tp', 0)}`", inline=True)
            embed.add_field(name="Raison SMC", value=plan.get("reason", "N/A"), inline=False)
            embed.set_footer(text="TradeAgent Hub - Decision Engine")

            if image_bytes:
                file = discord.File(io.BytesIO(image_bytes), filename="chart_analysis.png")
                embed.set_image(url="attachment://chart_analysis.png")
                await channel.send(file=file, embed=embed)
            else:
                await channel.send(embed=embed)

            print(f"✅ Notification transmise avec succès sur Discord (#{channel.name}).")
        except Exception as e:
            print(f"❌ Erreur lors de l'envoi Discord : {e}")

    def send_plan(self, plan: dict, image_bytes: bytes = None):
        """Méthode synchrone pour la compatibilité."""
        asyncio.create_task(self.send_plan_notification(plan, image_bytes))
