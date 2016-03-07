import itertools, sqlite3
from functools import cmp_to_key, lru_cache
from datetime import datetime
from collections import namedtuple
from enum import Enum
from werkzeug import check_password_hash, generate_password_hash
from flask import g, url_for

from import_tools import slugify, get_wikipedia_urls, get_palette

# TODO: Modify query_db so "[i for (i,) in." is unnecessary.

class NotFound(Exception):
    pass
    
class AlreadyExists(Exception):
    pass
    
def now_isoformat():
    return datetime.now().isoformat()

def connect_db():
    db = sqlite3.connect("sample.db")
    db.row_factory = sqlite3.Row
    return db

def detect_collision(slug_candidate, db, table):
    result = db.execute('select count(*) from {} where slug=?'.format(table), (slug_candidate,)).fetchall()
    if result[0][0] > 0:
        return True
    return False

def avoid_collison(name, db, table):
    if not detect_collision(name, db, table):
        return name

    for i in itertools.count(1):
        slug = "%s-%d" % (name, i)
        
        if not detect_collision(slug, db, table):
            return slug

def generate_slug(text, db, table):
    slug_candidate = slugify(text)
    return avoid_collison(slug_candidate, db, table)
    
class ObjectType(Enum):
    artist = 1
    release = 2
    track = 3

class Model:
    def __init__(self, connect_db=connect_db):
        self.db = connect_db()
        
    def close(self):
        self.db.close()

    def query(self, query, *args):
        return self.db.execute(query, args).fetchall()
        
    def query_unique(self, query, *args):
        result = self.query(query, *args)
        
        if len(result) == 0:
            raise NotFound()
            
        elif len(result) != 1:
            raise Exception("Result wasn't unique, '%s' with %s" % (query, str(args)))
            
        return result[0]
        
    def execute(self, query, *args):
        self.query(query, *args)
        self.db.commit()
        
    def insert(self, query, *args):
        cursor = self.db.cursor()
        cursor.execute(query, args)
        self.db.commit()
        return cursor.lastrowid
        
    #IDs
    
    def new_id(self, type):
        return self.insert("insert into objects (type) values (?)", type.value)
        
    #Artists
    
    Artist = namedtuple("Artist", ["id", "name", "slug", "releases", "get_image_url", "get_description", "get_wikipedia_urls"])
        
    def add_artist(self, name, description, incomplete=None):
        #Todo document "incomplete"
        
        slug = generate_slug(name, self.db, "artists")
        
        artist_id = self.new_id(ObjectType.artist)
        self.insert("insert into artists (id, name, slug, incomplete) values (?, ?, ?, ?)",
                    artist_id, name, slug, incomplete)
        
        self.insert("insert into descriptions (id, description) values (?, ?)",
                    artist_id, description)
                    
        return artist_id
        
    def _make_artist(self, row):
        def get_artist_wikipedia_urls():
            try:
                return get_wikipedia_urls(self.get_link(row["id"], "wikipedia"))
            
            except NotFound:
                return None
        
    
        #Always need to know the releases, might as well get them eagerly
        return self.Artist(*row,
            releases=self.get_releases_by_artist(row["id"], row["slug"]),
            get_image_url=lambda: None,
            get_description=lambda: self.get_description(row["id"]),
            get_wikipedia_urls=get_artist_wikipedia_urls
        )
        
    def get_artist(self, artist):
        """Retrieve artist info by id or by slug"""
        
        query = "select id, name, slug from artists where %s=?" % ("slug" if isinstance(artist, str) else "id")
        return self._make_artist(self.query_unique(query, artist))
        
    def get_release_artists(self, release_id, primary_artist_id=None):
        """Get all the artists who authored a release"""
        
        artists = [
            self._make_artist(row) for row in
            self.query("select id, name, slug from"
                       " (select artist_id from authorships where release_id=?)"
                       " join artists on artist_id = artists.id", release_id)
        ]
        
        if primary_artist_id:
            #Put the primary artist first
            ((index, primary_artist),) = [(i, a) for i, a in enumerate(artists) if a.id == primary_artist_id]
            return [primary_artist] + artists[:index] + artists[index+1:]
        
        return artists
        
    #Releases
    
    Release = namedtuple("Release", ["id", "title", "slug", "date", "release_type", "full_art_url", "thumb_art_url",
                                     "url", "get_tracks", "get_artists", "get_palette", "get_rating_stats"])
    
    #Handle selection/renaming for joins
    _release_columns = "release_id, title, slug, date, type, full_art_url, thumb_art_url"
    _release_columns_rename = "releases.id as release_id, title, slug, date, type, full_art_url, thumb_art_url"
    #todo rename the actual columns

    def add_release(self, title, date, type, full_art_url, thumb_art_url, mbid):
        slug = generate_slug(title, self.db, "releases")
        
        release_id = self.new_id(ObjectType.release)
        self.insert("insert into releases (id, title, slug, date, type, full_art_url, thumb_art_url)"
                    " values (?, ?, ?, ?, ?, ?, ?)", release_id, title, slug, date, type, full_art_url, thumb_art_url)

        self.add_palette_from_image(release_id, thumb_art_url)
        self.add_link(release_id, "musicbrainz", mbid)
                    
        return release_id
        
    def add_author(self, release_id, artist_id):
        self.insert("insert into authorships (release_id, artist_id) values (?, ?)",
                    release_id, artist_id)
    
    def _make_release(self, row, primary_artist_id=None, primary_artist_slug=None):
        release_id = row[0]
        release_slug = row[2]
        
        if not primary_artist_id:
            primary_artist_id, primary_artist_slug = \
                self.query_unique("select id, slug from"
                                  " (select artist_id from authorships where release_id=?)"
                                  " join artists on artist_id = artists.id limit 1", release_id)
        
        elif not primary_artist_slug:
            primary_artist_slug = self.query_unique("select slug from artists where id=?", primary_artist_id)
        
        return self.Release(*row,
            url=url_for("release_page", release_slug=release_slug, artist_slug=primary_artist_slug),
            get_artists=lambda: self.get_release_artists(release_id, primary_artist_id),
            get_palette=lambda: self.get_palette(release_id),
            get_tracks=lambda: self.get_release_tracks(release_id),
            get_rating_stats=lambda: self.get_rating_stats(release_id)
        )
        
    def get_releases_by_artist(self, artist_id, artist_slug=None):
        """artist_slug is optional but saves having to look it up"""
        
        return [
            self._make_release(row, artist_id, artist_slug) for row in
            self.query("select " + self._release_columns + " from"
                       " (select release_id from authorships where artist_id=?)"
                       " join releases on releases.id = release_id", artist_id)
        ]
        
    def get_releases_rated_by_user(self, user_id, rating=None):
        """Get all the releases rated by a user, and only those rated
           a certain value, if given"""
        return [
            self._make_release(row) for row in
            self.query("select " + self._release_columns + " from"
                       " (select object_id from ratings where user_id=?"
                       + (" and rating=?)" if rating else ")") +
                       " join releases on releases.id = object_id", user_id, rating)
        ]
        
    def get_release(self, artist_slug, release_slug):
        #Select the artist and release rows with the right slugs
        # (first, to make the join small)
        #Join them using authorships
        artist_id, *row = \
            self.query_unique("select artist_id, " + self._release_columns + " from"
                              " (select artists.id as artist_id from artists where artists.slug=?)"
                              " natural join authorships natural join"
                              " (select " + self._release_columns_rename + " from releases where releases.slug=?)",
                              artist_slug, release_slug)

        return self._make_release(row, artist_id, artist_slug)
        
    #Tracks
    
    Track = namedtuple("Track", ["title", "runtime"])
    
    def add_track(self, release_id, title, position, runtime):
        slug = generate_slug(title, self.db, "tracks")
        
        track_id = self.new_id(ObjectType.track)
        self.insert("insert into tracks (id, release_id, title, slug, position, runtime) values (?, ?, ?, ?, ?, ?)",
                    track_id, release_id, title, slug, position, runtime)

    def get_release_tracks(self, release_id):
        total_runtime = None
        
        def runtime(milliseconds):
            if milliseconds:
                nonlocal total_runtime
                total_runtime = milliseconds + (total_runtime or 0)
                return "%d:%02d" % (milliseconds//60000, (milliseconds/1000) % 60)
        
        #todo sort by position
        return [
            self.Track(title, runtime(milliseconds)) for title, milliseconds
            in self.query("select title, runtime from tracks where release_id=?", release_id)
        ], runtime(total_runtime)

    #Object attachments
    
    def add_palette_from_image(self, id, image_url=None):
        palette = get_palette(image_url) if image_url else [None, None, None]
        self.insert("replace into palettes (id, color1, color2, color3)"
                    " values (?, ?, ?, ?)", id, *palette)
        
    def get_palette(self, id):
        return self.query_unique("select color1, color2, color3 from palettes where id=?", id)
        
    def get_description(self, id):
        return self.query_unique("select description from descriptions where id = (?)", id)[0]

    @lru_cache(maxsize=128)
    def _get_link_type_id(self, link_type):
        try:
            return self.query_unique("select id from link_types where type=?", link_type)[0]
            
        except NotFound:
            return self.insert("insert into link_types (type) values (?)", link_type)
        
    def add_link(self, id, link_type, target):
        self.insert("insert into links (id, type_id, target)"
                    " values (?, ?, ?)", id, self._get_link_type_id(link_type), target)

    def get_link(self, id, link_type):
        """link_type can either be the string that identifies a link, or its id"""
        
        link_type_id = self._get_link_type_id(link_type) if isinstance(link_type, str) else link_type
    
        return self.query_unique("select target from links"
                                 " where id=? and type_id=?", id, link_type_id)[0]
        
    #Ratings
    
    RatingStats = namedtuple("RatingStats", ["average", "frequency"])
        
    def set_rating(self, object_id, user_id, rating):
        self.execute("replace into ratings (object_id, user_id, rating, creation)"
                     " values (?, ?, ?, ?)", object_id, user_id, rating, now_isoformat())

    def unset_rating(self, object_id, user_id):
        # TODO: Error if no rating present?
        self.execute("delete from ratings"
                     " where object_id=? and user_id=?", object_id, user_id)
        
    def get_rating_stats(self, object_id):
        try:
            ratings = [r for (r,) in self.query("select rating from ratings where object_id=?", object_id)]
            frequency = len(ratings)
            average = sum(ratings) / frequency
            return self.RatingStats(average=average, frequency=frequency)
            
        except ZeroDivisionError:
            return self.RatingStats(average=None, frequency=0)
        
    #Users
    
    User = namedtuple("User", ["id", "name", "creation", "ratings", "get_releases_rated"])
    
    def _make_user(self, _id, name, creation, ratings):
        return self.User(_id, name, creation, ratings,
            get_releases_rated=lambda rating=None: self.get_releases_rated_by_user(_id, rating)
        )
    
    def get_user(self, user):
        """Get user by id or by slug"""
        
        query =   "select id, name, creation from users where %s=?" \
                % ("name" if isinstance(user, str) else "id")
        user_id, name, creation = self.query_unique(query, user)
        
        ratings = dict(self.query("select object_id, rating from ratings"
                                  " where ratings.user_id=?", user_id))
                                
        return self._make_user(user_id, name, creation, ratings)
        
    def register_user(self, name, password, email=None, fullname=None):
        """Try to add a new user to the database.
           Perhaps counterintuitively, for security hashing the password is
           delayed until this function. Better that you accidentally hash
           twice than hash zero times and store the password as plaintext."""
    
        if self.query("select id from users where name=?", name):
            raise AlreadyExists()
            
        creation = now_isoformat()
        user_id = self.insert("insert into users (name, pw_hash, email, fullname, creation) values (?, ?, ?, ?, ?)",
                              name, generate_password_hash(password), email, fullname, creation)
                       
        return self._make_user(user_id, name, creation, {})
    
    def user_pw_hash_matches(self, given_password, user_slug):
        """For security, the hash is never stored anywhere except the databse.
           For added security, it doesn't even leave this function."""
        user_id, db_hash = self.query_unique("select id, pw_hash from users where name=?", user_slug)
        matches = check_password_hash(db_hash, given_password)
        return (matches, user_id if matches else None)
