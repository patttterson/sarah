import os
import pkgutil
import time
from typing import Literal

import asqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import sarah.cogs
from sarah.db.settings import get_setting
from sarah.errors import report_error

load_dotenv()
os.environ["JISHAKU_NO_UNDERSCORE"] = "true"


def cog_names():
    names = [
        f"sarah.cogs.{m.name}"
        for m in pkgutil.iter_modules(sarah.cogs.__path__)
        if not m.name.startswith("_")
    ]
    names.sort(key=lambda name: name != "sarah.cogs.auth")
    return names

def full_name(ext):
    return ext if "."in ext else f"sarah.cogs.{ext}"

class Bot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.twitch_client_id = os.getenv("TWITCH_CLIENT_ID")
        self.twitch_client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.twitch_redirect_uri = os.getenv("TWITCH_REDIRECT_URI")
        self.twitch_access_token: dict = {}  # token, expires_at

        self.ign_cache: dict = {}

        self.log_channel: discord.TextChannel | None = None

        self.port = int(os.getenv('PORT', '3000'))
        self.pool: asqlite.Pool | None = None

    async def log(self, message: str, urgent: bool = False):
        assert self.pool
        if not self.log_channel:
            channel_id = await get_setting(self.pool, 'log_channel')
            if not isinstance(channel_id, int):
                print(f"Warning, no valid log channel set. Dropped log: {message}")
                return
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    channel = None
            if not isinstance(channel, discord.TextChannel):
                print(f"Warning: log channel {channel_id} is missing or not a text channel.")
                return
            self.log_channel = channel

        await self.log_channel.send(
            message,
            allowed_mentions=discord.AllowedMentions.all()
            if urgent
            else discord.AllowedMentions.none(),
        )

    async def on_ready(self):
        print(f"Logged in as {self.user}")

    async def setup_hook(self):
        self.pool = await asqlite.create_pool("bot.db")

        await self.load_extension("jishaku")
        for name in cog_names():
            await self.load_extension(name)

    async def close(self):
        try:
            await super().close()
        finally:
            if self.pool:
                await self.pool.close()


intents = discord.Intents.all()
bot = Bot(command_prefix="s.", intents=intents)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    await report_error(interaction, getattr(error, "original", error))


@bot.command(hidden=True)
@commands.guild_only()
@commands.is_owner()
async def sync(
    ctx: commands.Context,
    guilds: commands.Greedy[discord.Object],
    spec: Literal["~", "*", "^"] | None = None,
) -> None:
    if not guilds:
        if spec == "~":
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else:
            synced = await ctx.bot.tree.sync()

        await ctx.send(
            f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
        )
        return

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException:
            pass
        else:
            ret += 1

    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

@bot.command(hidden=True)
@commands.is_owner()
async def load(ctx, extension):
    names = cog_names() if extension == "~" else [full_name(extension)]
    for name in names:
        await bot.load_extension(name)
    await ctx.send("\n".join(f"\U000023eb {name}" for name in names))

@bot.command(hidden=True)
@commands.is_owner()
async def unload(ctx, extension):
    names = cog_names() if extension == "~" else [full_name(extension)]
    for name in names:
        await bot.unload_extension(name)
    await ctx.send("\n".join(f"\U000023ec {name}" for name in names))


@bot.command(hidden=True)
@commands.is_owner()
async def reload(ctx, extension="~"):
    start = time.perf_counter()
    names = cog_names() if extension == "~" else [full_name(extension)]
    for name in names:
        try:
            await bot.reload_extension(name)
        except commands.ExtensionNotLoaded:
            await bot.load_extension(name)
    lines = "\n".join(f"\U0001f501 {name}" for name in names)
    await ctx.send(f"{lines}\ntook {time.perf_counter() - start:.2f}s")


os.environ["JISHAKU_NO_UNDERSCORE"] = "true"

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if token is None:
        raise SystemExit("DISCORD_BOT_TOKEN environment variable not set.")
    bot.run(token)
