import time
from importlib.metadata import version
from typing import TYPE_CHECKING

import aiohttp
import discord
from aiohttp import ClientSession
from discord import app_commands
from discord.ext import commands, tasks

from sarah.bot import Bot
from sarah.db.settings import get_setting
from sarah.errors import PredictionError, TwitchAPIError, TwitchError, report_error

if TYPE_CHECKING:
    from sarah.cogs.auth import Auth

STATUS_WORDS = {'RESOLVED': 'resolved', 'CANCELED': 'cancelled', 'LOCKED': 'locked'}

__version__ = version("sarah")

class Predictions(commands.Cog):
    def __init__(self, bot: Bot):
        # key: discord user id, value: {ign, expires_at}
        self.bot = bot
        self.session: ClientSession | None = None
        self.prediction_id: str | None = None
        self.outcome_ids = {}

    async def cog_load(self):
        self.session = ClientSession()
        self.load_prediction.start()

    async def cog_unload(self):
        if self.session:
            await self.session.close()
        self.load_prediction.cancel()
            
    @tasks.loop(count=1)
    async def load_prediction(self):
        assert self.bot.pool
        
        broadcaster_id = await get_setting(self.bot.pool, 'broadcaster_id', '')
        if not broadcaster_id: return
        
        auth: Auth | None = self.bot.get_cog("Auth") # type: ignore
        if auth is None:
            print("Auth cog isn't loaded, can't restore prediction")
            return    
    
        try:
            data = await auth.twitch_request('GET', 'https://api.twitch.tv/helix/predictions',
                                             params={'broadcaster_id': str(broadcaster_id), 'first': 1})
        except (TwitchError, aiohttp.ClientError, TimeoutError) as e:
            print(f"Couldn't restore current prediction: {e}")
            return

        predictions = data.get('data')
        if not predictions or predictions[0]['status'] not in ("ACTIVE", "LOCKED"):
            return

        prediction = predictions[0]
        self.prediction_id = prediction['id']
        self.outcome_ids = {o['title']:o['id'] for o in prediction['outcomes']}
        print(f"Restored {prediction['status'].lower()} prediction {self.prediction_id}")
    
    @load_prediction.before_loop
    async def before_load(self):
        await self.bot.wait_until_ready()

    async def get_ign(self, user_id: int) -> str | None:
        if not self.session:
            raise RuntimeError("HTTP session not initialized.")

        ign = self.bot.ign_cache.get(user_id)
        if self.bot.ign_cache.get("expires_at", 0) < time.time():
            self.bot.ign_cache.pop(user_id, None)
            ign = None
        if ign: return ign

        # this loop exists so that you can "break" out of the with statement
        for _ in (True,):
            async with self.session.get(f"https://ch.tetr.io/api/users/search/discord:id:{user_id}", headers={'User-Agent': f"Sarah / {__version__} (hi osk https://github.com/patttterson/sarah)", 'Content-Type': 'application/json'}) as resp:
                data = await resp.json()
                if resp.status != 200 or not data.get('success'):
                    print("Failed to fetch TETR.IO user.")
                    break
                users = data.get('data').get('users')
                if len(users) != 1:
                    break
                ign = users[0].get('username')

        if ign: return ign

        print(f"No IGN was found for discord id {user_id}.")
        return None

    async def ign_followup(self, interaction: discord.Interaction, ign1: str, ign2: str, user1: discord.Member, user2: discord.Member, prediction_window: int | None):
        assert self.session
        assert self.bot.pool
        assert self.bot.twitch_client_id

        broadcaster_id = await get_setting(self.bot.pool, 'broadcaster_id')

        if not isinstance(interaction.channel, discord.abc.Messageable):
            return await interaction.response.send_message("How did you call this command from here?", ephemeral=True)
        await interaction.channel.send(f"**{user1.mention} ({ign1}) and {user2.mention} ({ign2}) up now!** Please join the stream room ASAP <a:foxspeen:986616714924011560>")

        auth: Auth | None = self.bot.get_cog("Auth") # type: ignore
        if auth is None:
            raise RuntimeError("Auth cog is not loaded.")

        try:
            data = await auth.twitch_request('POST', 'https://api.twitch.tv/helix/predictions', json={
                'broadcaster_id': broadcaster_id,
                'title': await get_setting(self.bot.pool, 'prediction_title', 'Who Wins?'),
                'outcomes': [{'title': ign1}, {'title': ign2}],
                'prediction_window': prediction_window or await get_setting(self.bot.pool, 'prediction_window', 120)
            })
            prediction = data["data"][0]
        except TwitchAPIError as e:
            raise PredictionError(e.status, e.message) from e

        self.outcome_ids[ign1] = prediction['outcomes'][0]['id']
        self.outcome_ids[ign2] = prediction['outcomes'][1]['id']
        self.prediction_id = prediction['id']

        await interaction.response.send_message(f"Prediction between {ign1} and {ign2} started!", ephemeral=True)
        await self.bot.log(f"Prediction between {ign1} and {ign2} started by {interaction.user.mention} (`{prediction['id']}`)")

    @app_commands.command(name="start-prediction", description="Start a new prediction.")
    @app_commands.rename(user1='player-1', user2='player-2', prediction_window='submission-period')
    @app_commands.describe(user1='The first user to stream')
    @app_commands.describe(user2='The second user to stream')
    @app_commands.describe(prediction_window="How long chatters should be able to submit, in seconds")
    @app_commands.default_permissions(manage_guild=True)
    async def start_prediction(self, interaction: discord.Interaction, *, user1: discord.Member, user2: discord.Member, prediction_window: int | None):
        ign1, ign2 = await self.get_ign(user1.id), await self.get_ign(user2.id)
        if not ign1 or not ign2:
            await interaction.response.send_modal(IGNModal(self, ign1, ign2, user1, user2, prediction_window))
        else:
            await self.ign_followup(interaction, ign1, ign2, user1, user2, prediction_window)

    @app_commands.command(name="resolve-prediction", description="Resolve the current prediction")
    @app_commands.default_permissions(manage_guild=True)
    async def resolve_prediction(self, interaction: discord.Interaction, *, winner: str):
        if winner not in self.outcome_ids.values():
            return await interaction.response.send_message(
                "That isn't one of the current prediction's outcomes. Please pick one from the list.",
                ephemeral=True
            )
        await self.end_prediction(interaction, 'RESOLVED', winner_id=winner)

    @resolve_prediction.autocomplete('winner')
    async def prediction_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=ign, value=outcome_id)
            for ign, outcome_id in self.outcome_ids.items()
            if current.lower() in ign.lower()
        ]

    @app_commands.command(name="cancel-prediction", description="Cancel the current prediction")
    @app_commands.default_permissions(manage_guild=True)
    async def cancel_prediction(self, interaction: discord.Interaction):
        await self.end_prediction(interaction, 'CANCELED')

    @app_commands.command(name="lock-prediction", description="Lock the current prediction.")
    @app_commands.default_permissions(manage_guild=True)
    async def lock_prediction(self, interaction: discord.Interaction):
        await self.end_prediction(interaction, 'LOCKED')

    async def end_prediction(self, interaction: discord.Interaction, status: str, winner_id: str | None = None):
        assert self.bot.pool

        broadcaster_id = await get_setting(self.bot.pool, 'broadcaster_id')

        auth: Auth | None = self.bot.get_cog("Auth") # type: ignore
        if auth is None:
            raise RuntimeError("Auth cog is not loaded.")

        try:
            if status != 'RESOLVED':
                data = await auth.twitch_request('PATCH', 'https://api.twitch.tv/helix/predictions', json={
                    'broadcaster_id': broadcaster_id,
                    'id': self.prediction_id,
                    'status': status
                })
            else:
                data = await auth.twitch_request('PATCH', 'https://api.twitch.tv/helix/predictions', json={
                    'broadcaster_id': broadcaster_id,
                    'id': self.prediction_id,
                    'status': status,
                    'winning_outcome_id': winner_id
                })
        except TwitchAPIError as e:
            raise PredictionError(e.status, e.message) from e

        outcomes = data.get('data')[0]['outcomes']
        msg = f"Prediction between {outcomes[0]['title']} and {outcomes[1]['title']} {STATUS_WORDS[status]}."
        self.prediction_id = None
        self.outcome_ids.clear()
        await interaction.response.send_message(msg, ephemeral=True)
        await self.bot.log(msg)

class IGNModal(discord.ui.Modal, title='IGN Input'):
    def __init__(self, cog, ign1, ign2, user1, user2, prediction_window):
        super().__init__()
        self.cog = cog
        self.ign1 = ign1
        self.ign2 = ign2
        self.user1, self.user2 = user1, user2
        self.prediction_window = prediction_window

        if not ign1:
            self.ign1_input = discord.ui.TextInput(label='Player 1 IGN', placeholder='Player 1 here...')
            self.add_item(self.ign1_input)
        if not ign2:
            self.ign2_input = discord.ui.TextInput(label='Player 2 IGN', placeholder='Player 2 here...')
            self.add_item(self.ign2_input)

    async def on_submit(self, interaction: discord.Interaction):
        ign1 = self.ign1 or self.ign1_input.value
        ign2 = self.ign2 or self.ign2_input.value
        await self.cog.ign_followup(interaction, ign1, ign2, self.user1, self.user2, self.prediction_window)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)

async def setup(bot: Bot):
    await bot.add_cog(Predictions(bot))
