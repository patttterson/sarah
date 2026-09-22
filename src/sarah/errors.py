import logging

import discord
from discord import app_commands

log = logging.getLogger(__name__)

class NotSetupError(Exception):
    """The bot is not set up properly."""

    def __init__(self, name: str):
        super().__init__(f"Required setting {name!r} is not set.")
        self.name = name

class TwitchError(app_commands.AppCommandError):
    """Base for all twitch-related errors."""

class MissingTokenError(TwitchError):
    """No refresh token is stored at all."""

class ReauthRequiredError(TwitchError):
    """The refresh token has expired for whatever reason."""

class TwitchAPIError(TwitchError):
    """Twitch returned an unexpected response."""

    def __init__(self, status: int, message: str = ""):
        super().__init__(f"Twitch API returned {status}: {message}")
        self.status = status
        self.message = message

class PredictionError(TwitchAPIError):
    """Twitch refused to do something with a prediction."""

async def report_error(interaction: discord.Interaction, error: Exception):
    if isinstance(error, NotSetupError):
        if error.name == "broadcaster_id":
            msg = "The Twitch account isn't linked. The account owner needs to run </setup:1550326746471997516>."
        else:
            msg = f"The bot isn't fully set up yet: `{error.name}` hasn't been configured. Ask an admin to set it."
    elif isinstance(error, (MissingTokenError, ReauthRequiredError)):
        msg = "The Twitch account isn't linked. The account owner needs to run </setup:1550326746471997516>."
    elif isinstance(error, PredictionError):
        msg = f"Twitch couldn't start the prediction: {error.status} {error.message}"
    elif isinstance(error, TwitchAPIError):
        msg = "Twitch is having problems, please check https://status.twitch.tv/"
    else:
        msg = "Something went wrong."

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

    if not isinstance(error, TwitchError) and not isinstance(error, NotSetupError):
        name = interaction.command.qualified_name if interaction.command else "unknown"
        log.error("Unhandled error in /%s", name, exc_info=error)