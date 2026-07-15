# Authored By Iota Coders © 2025
from IotaXMedia.core.bot import MusicBotClient
from IotaXMedia.core.dir import StorageManager
from IotaXMedia.core.git import git
from IotaXMedia.core.userbot import Userbot
from IotaXMedia.misc import dbb, heroku

from .logging import LOGGER

StorageManager()
git()
dbb()
heroku()

app = MusicBotClient()
userbot = Userbot()


from .platforms import *

Apple = AppleAPI()
Carbon = CarbonAPI()
SoundCloud = SoundAPI()
Spotify = SpotifyAPI()
Resso = RessoAPI()
Telegram = TeleAPI()
YouTube = YouTubeAPI()
