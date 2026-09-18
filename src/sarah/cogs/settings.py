from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from sarah.bot import Bot
from sarah.db.settings import get_setting, set_setting

if TYPE_CHECKING:
    from sarah.cogs.auth import Auth

class Settings(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    settings_group = app_commands.Group(
        name='settings',
        description='Group for settings. If no value is provided, will read out the current value.',
        allowed_contexts=app_commands.AppCommandContext(guild=True, private_channel=False),
        default_permissions=discord.Permissions(manage_guild=True)
    )

    @settings_group.command(name='prediction-window', description='Change how long predictions last before closing.')
    async def set_prediction_window(self, interaction: discord.Interaction, *, seconds: app_commands.Range[int, 30, 1800] | None):
        assert self.bot.pool

        if seconds is None:
            return await interaction.response.send_message(f"By default, predictions will last {await get_setting(self.bot.pool, 'prediction_window', 120)} seconds!", ephemeral=True)

        if seconds < 30 or seconds > 1800:
            return await interaction.response.send_message(f"The time you input, {seconds}, is invalid. The minimum is **30** seconds and the maximum is **1800** seconds (30 minutes). Please try again.")
        await set_setting(self.bot.pool, 'prediction_window', seconds)
        await interaction.response.send_message(f"The prediction run time has been updated to **{seconds}** seconds.")
        await self.bot.log(f"The prediction run time has been updated to **{seconds}** seconds by {interaction.user.mention}.")

    @settings_group.command(name='prediction-title', description='Change the title of the prediction, e.g. "Who W?"')
    async def set_prediction_title(self, interaction: discord.Interaction, *, title: app_commands.Range[str, 1, 45] | None):
        assert self.bot.pool

        if title is None:
            return await interaction.response.send_message(f"The current title for predictions is \"{await get_setting(self.bot.pool, 'prediction_title', 'Who Wins?')}\"", ephemeral=True)

        if len(title) > 45 or len(title) < 1:
            return await interaction.response.send_message(f"The title you input, {title}, is invalid. The title is limited to a maximum of 45 characters. Please try again.")
        await set_setting(self.bot.pool, 'prediction_title', title)
        await interaction.response.send_message(f"The prediction title has been set to: \"**{title}**\"")
        await self.bot.log(f"The prediction title has been set by {interaction.user.mention} to: \"**{title}**\"")

    @settings_group.command(name='broadcaster-id', description='Update the broadcaster ID internally. This is quite rare.')
    async def update_broadcaster_id(self, interaction: discord.Interaction):
        assert self.bot.pool
        assert self.bot.twitch_client_id

        auth: Auth | None = self.bot.get_cog("Auth") # type: ignore[assignment]
        if auth is None:
            return await interaction.response.send_message("The Authentication module is not loaded.")

        await interaction.response.defer()
        broadcaster_id, display_name = await auth.fetch_user()

        await set_setting(self.bot.pool, 'broadcaster_id', broadcaster_id)
        await interaction.followup.send(f"Broadcaster ID updated to {broadcaster_id} ({display_name})")
        await self.bot.log(f"The broadcaster id has been refreshed and updated to {broadcaster_id} ({display_name}) by {interaction.user.mention}.")

    @settings_group.command(name='log-channel', description='Change the channel where the bot puts its logs.')
    async def set_log_channel(self, interaction: discord.Interaction, *, channel: discord.TextChannel | None):
        assert self.bot.pool

        if not interaction.guild:
            return await interaction.response.send_message("This command must be called from a server.", ephemeral=True)

        if not channel:
            current_channel = await get_setting(self.bot.pool, 'log_channel')
            if not current_channel:
                return await interaction.response.send_message("There is currently no log channel set. Please set one.")
            if not isinstance(current_channel, int):
                return await interaction.response.send_message("There is something wrong with the log channel. Contact the developer.", ephemeral=True)
            current_channel = self.bot.get_channel(current_channel)
            if not isinstance(current_channel, discord.abc.Messageable):
                return await interaction.response.send_message("The log channel appears to be broken. Please contact the developer.", ephemeral=True)
            return await interaction.response.send_message(f"The current log channel is {current_channel.mention}.", ephemeral=True)

        channel_perms = channel.permissions_for(interaction.guild.me)
        if not channel_perms.send_messages:
            return await interaction.response.send_message(f"I am currently unable to send messages in {channel.mention}. Please check the permissions and try again.", ephemeral=True)

        await set_setting(self.bot.pool, 'log_channel', channel.id)
        self.bot.log_channel = channel
        await interaction.response.send_message(f"Log channel updated to {channel.mention}.")
        await self.bot.log(f"The log channel has been updated to {channel.mention} by {interaction.user.mention}.")

async def setup(bot: Bot):
    await bot.add_cog(Settings(bot))