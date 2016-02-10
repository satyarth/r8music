from functools import partial as p
from db import query_db

# TODO: Properly encapsulate the db.


DB_NAME = 'sample.db'


def lmap(*args, **kwargs):
    return list(map(*args, **kwargs))


def db_results(*args, **kwargs):
    with sqlite3.connect(DB_NAME) as conn:
        return list(conn.cursor().execute(*args, **kwargs))

class NotFound(Exception):
    pass
    
class ArtistNotFound(NotFound):
    pass
    
class ReleaseNotFound(NotFound):
    pass

class UserNotFound(NotFound):
    pass

class Artist(object):
    def __init__(self, _id):
        ((self._id, self.name, self.slug, self.incomplete),) = query_db(
                'select * from artists where id=?', (_id,))
        self.release_ids = [i for (i,) in query_db(
                'select release_id from authors where artist_id=?', (_id,))]
        self.releases = lmap(p(Release, self), self.release_ids)

    @classmethod
    def from_slug(cls, slug):
        try:
            ((_id,),) = query_db(
                    'select id from artists where slug=?', (slug,))
            return cls(_id)
        
        except ValueError:
            raise ArtistNotFound()


class Release(object):
    def __init__(self, artist, _id):
        self.artist = artist
        artist_ids = [i for (i,) in query_db('select artist_id from authors where release_id=?', (_id,))]
        ((self._id,
          self.title,
          self.slug,
          self.date,
          self.reltype,
          self.album_art_url),) = \
                query_db('select * from releases where id=?', (_id,))
        self.tracks = lmap(p(Track, self), [t for (t,) in query_db(
                'select id from tracks where release_id=?', (self._id,))])
        (self.colors, ) = query_db('select color1, color2, color3 from release_colors where release_id=?', (_id,))

    @classmethod
    def from_slug(cls, artist, release_slug):
        try:
            ((_id,),) = query_db(
                    'select id from releases where slug=?', (release_slug,))
            return cls(artist, _id)
            
        except ValueError:
            raise ReleaseNotFound()

    @classmethod
    def from_slugs(cls, artist_slug, release_slug):
        return cls.from_slug(Artist.from_slug(artist_slug), release_slug)


class Track(object):
    def __init__(self, release, _id):
        self.release = release
        ((self._id,
          self._release_id,
          self.title,
          self.slug,
          self.position,
          self.runtime),) = \
                  query_db('select * from tracks where id=?', (_id,))
        if self.runtime:
            self.runtime_string = str(self.runtime//60000) + ":" + str(int(self.runtime/1000) % 60).zfill(2)
        else:
            self.runtime_string = "??:??"

    def __repr__(self):
        return self.title


class User(object):
    def __init__(self, _id):
        try:
            ((self._id,
              self.name),) = \
                      query_db('select * from users where id=?', (_id,))
        except ValueError:
            raise UserNotFound()
            
        self.ratings = dict(query_db('select release_id, rating from ratings where ratings.user_id=?', (_id,)))
