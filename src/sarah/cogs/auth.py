import asyncio
import secrets
import time
import urllib.parse

import discord
from aiohttp import ClientSession, web
from discord import app_commands
from discord.ext import commands, tasks

from sarah.bot import Bot
from sarah.db.settings import set_setting
from sarah.db.token_store import get_refresh_token, save_refresh_token
from sarah.errors import MissingTokenError, ReauthRequiredError, TwitchAPIError


class Auth(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.pending_states = {}
        self.runner: web.AppRunner | None = None
        self.session: ClientSession | None = None

        self.refresh_lock = asyncio.Lock()

    async def cog_load(self):
        assert self.bot.pool
        self.session = ClientSession()
        app = web.Application()
        app.router.add_get('/callback', self.oauth_callback)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, '127.0.0.1', self.bot.port).start()

        self.validate_token.start()

        if not self.bot.twitch_access_token and await get_refresh_token(self.bot.pool):
            await self.refresh("", force=True)

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

        self.validate_token.cancel()

    async def oauth_callback(self, request: web.Request) -> web.Response:
        if not self.session:
            return web.Response(text="Server not initialized properly.", status=500)

        if not self.bot.twitch_client_id:
            return web.Response(text="The variables for the server have not been set up properly. Please contact the developer.", status=500)

        code = request.query.get('code')
        state = request.query.get('state')
        user_id = self.pending_states.get(state)
        if not code or not state or not user_id:
            self.pending_states.pop(state, None)
            return web.Response(text="Invalid request. Missing code, state, or user ID.", status=400)

        async with self.session.post("https://id.twitch.tv/oauth2/token", data={
            'client_id': self.bot.twitch_client_id,
            'client_secret': self.bot.twitch_client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.bot.twitch_redirect_uri
        }) as resp:
            if resp.status != 200:
                return web.Response(text="Failed to exchange code for token.", status=500)
            data = await resp.json()
            access_token = data.get('access_token')
            refresh_token = data.get('refresh_token')
            expires_in = data.get('expires_in')

        del self.pending_states[state]

        if not access_token or not refresh_token:
            return web.Response(text="Failed to retrieve access or refresh token.", status=500)

        if not self.bot.pool:
            return web.Response(text="Database connection not initialized.", status=500)
        await save_refresh_token(self.bot.pool, refresh_token)

        self.bot.twitch_access_token = {'token': access_token, 'expires_at': int(time.time()) + expires_in}

        try:
            broadcaster_id, display_name = await self.fetch_user()
        except TwitchAPIError:
            return web.Response(text="An error occured while fetching your user.", status=500)

        await self.bot.log(f"<@{user_id}> has linked their twitch, {display_name} (ID: {broadcaster_id})")

        return web.Response(text="Authentication successful! You can now close this window.", status=200)

    async def twitch_request(self, method: str, url: str, **kwargs):
        assert self.session
        assert self.bot.twitch_client_id
        token = self.bot.twitch_access_token.get('token') or await self.refresh("", force=True)
        for attempt in range(2):
            async with self.session.request(method, url, headers={
                'Authorization': f"Bearer {token}",
                'Client-Id': self.bot.twitch_client_id
            }, **kwargs) as resp:
                if resp.status == 401 and attempt == 0:
                    token = await self.refresh(token)
                    await asyncio.sleep(1.0)
                    continue
                data = await resp.json()
                if resp.status >= 400:
                    raise TwitchAPIError(resp.status, data.get("message", ""))
                return data
        raise ReauthRequiredError()

    async def fetch_user(self):
        assert self.bot.pool
        data = await self.twitch_request('GET', 'https://api.twitch.tv/helix/users')
        users = data.get('data')
        if not users:
            raise TwitchAPIError(200, "Empty, no data")
        broadcaster_id, display_name = users[0]['id'], users[0]['display_name']
        await set_setting(self.bot.pool, 'broadcaster_id', broadcaster_id)
        return broadcaster_id, display_name

    async def refresh(self, stale_token: str | None, force: bool = False):
        async with self.refresh_lock:
            if (
                not force
                and self.bot.twitch_access_token
                and self.bot.twitch_access_token.get('token') != stale_token
            ):
                print("Stale token does not match the current token. Skipping refresh.")
                return self.bot.twitch_access_token.get('token')

            assert self.session
            assert self.bot.pool

            refresh_token = await get_refresh_token(self.bot.pool)
            if not refresh_token:
                raise MissingTokenError()

            async with self.session.post("https://id.twitch.tv/oauth2/token", data={
                'client_id': self.bot.twitch_client_id,
                'client_secret': self.bot.twitch_client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token'
            }) as resp:
                if 400 <= resp.status <= 500:
                    raise ReauthRequiredError()
                if resp.status != 200:
                    raise TwitchAPIError(resp.status, await resp.text())
                data = await resp.json()
                new_access_token = data.get('access_token')
                new_refresh_token = data.get('refresh_token')
                expires_in = data.get('expires_in')

            if not new_access_token or not new_refresh_token:
                raise TwitchAPIError(resp.status, await resp.text())

            await save_refresh_token(self.bot.pool, new_refresh_token)
            self.bot.twitch_access_token = {'token': new_access_token, 'expires_at': int(time.time()) + expires_in}
            return new_access_token

    @app_commands.command(name="setup", description="Authenticate your twitch account with the bot")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def twitch_setup(self, interaction: discord.Interaction):
        if not hasattr(self.bot, "twitch_client_id") or not hasattr(self.bot, "twitch_redirect_uri"):
            await interaction.response.send_message("Twitch client ID or redirect URI is not set up in the bot.", ephemeral=True)
            return

        state = secrets.token_urlsafe(16)
        self.pending_states[state] = interaction.user.id

        oauth_url = "https://id.twitch.tv/oauth2/authorize"
        params = {
            'client_id': self.bot.twitch_client_id,
            'redirect_uri': self.bot.twitch_redirect_uri,
            'response_type': 'code',
            'scope': 'channel:manage:predictions',
            'state': state
        }

        query_string = urllib.parse.urlencode(params)
        full_url = f"{oauth_url}?{query_string}"
        await interaction.response.send_message(f"Click [here](<{full_url}>) to authenticate your Twitch account. "
                                                 "**Make sure you are logged into the correct Twitch account before proceeding.**", ephemeral=True)

    @tasks.loop(hours=1)
    async def validate_token(self):
        print("validating twitch access token...")
        assert self.bot.pool
        if not await get_refresh_token(self.bot.pool):
            return

        if not self.session:
            raise RuntimeError("HTTP session not initialized.")

        token = self.bot.twitch_access_token.get('token')
        if not token:
            return await self.refresh("", force=True)
        async with self.session.get("https://id.twitch.tv/oauth2/validate", headers={
            'Authorization': f'Bearer {token}'
        }) as resp:
            if resp.status == 401:
                try:
                    await self.refresh(token)
                except (MissingTokenError, ReauthRequiredError):
                    await self.bot.log(f"<@{self.bot.owner_id}> Twitch access vanished, </setup:1550326746471997516> again.", urgent=True)
                except TwitchAPIError as e:
                    await self.bot.log(f"Twitch refresh failed temporarily ({e.status}), check <https://status.twitch.com/>")
            elif resp.status == 200:
                print(f"Token is valid! Expires at {self.bot.twitch_access_token.get('expires_at')}")
            else:
                await self.bot.log(f"<@{self.bot.owner_id}> something really weird happened while validating", urgent=True)

    @validate_token.before_loop
    async def before_validate_token(self):
        print("waiting to start validate_token loop...")
        await self.bot.wait_until_ready()

async def setup(bot: Bot):
    await bot.add_cog(Auth(bot))