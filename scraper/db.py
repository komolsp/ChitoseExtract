import os

from peewee import *

from app_paths import user_path

_CACHE_DIR = user_path('dlrenamer')
os.makedirs(_CACHE_DIR, exist_ok=True)
_CACHE_DB_PATH = os.path.join(_CACHE_DIR, 'cache.db')

db = SqliteDatabase(_CACHE_DB_PATH)


class WorkMetadataCache(Model):
    rjcode = CharField(primary_key=True)
    metadata = TextField()

    class Meta:
        database = db
