import musicbrainzngs
import sqlite3
import sys
import re
from unidecode import unidecode

# From http://flask.pocoo.org/snippets/5/
_punct_re = re.compile(r'[\t !"#$%&\'()*\-/<=>?@\[\\\]^_`{|},.]+')

def slugify(text, delim=u'-'):
    """Generates an ASCII-only slug."""
    result = []
    for word in _punct_re.split(text.lower()):
        result.extend(unidecode(word).split())
    return delim.join(result)

def get_releases(mbid):
    result = musicbrainzngs.get_artist_by_id(mbid, includes=['releases']) 
    releases = result['artist']['release-list']
    return releases

def get_tracks(release_id):
    result = musicbrainzngs.get_release_by_id(release_id, includes=['recordings'])
    tracks = result['release']['medium-list'][0]['track-list']
    return tracks

def import_artist(artist_name):
    result = musicbrainzngs.search_artists(artist=artist_name)
    artist_info = result['artist-list'][0]

    con = sqlite3.connect('sample.db')
    cursor = con.cursor()

    cursor.execute(
        "insert into artists (name, url) values (?, ?)",
        (artist_info["name"], slugify(artist_info["name"]))
    )

    artist_id = cursor.lastrowid
    releases = get_releases(artist_info['id'])

    for release in releases:
        cursor.execute(
            "insert into releases (title, year, artist_id) values (?, ?, ?)",
            (release['title'], int(release['date'][:4]), artist_id)
        )
        release['local-id'] = cursor.lastrowid
        try:
            tracks = get_tracks(release['id'])
            for track in tracks:
                cursor.execute(
                    "insert into tracks (title, runtime, release_id) values (?, ?, ?)",
                    (track['recording']['title'], track['recording']['length'], release['local-id'])
                )
        except:
            pass
    con.commit()

musicbrainzngs.set_useragent("Skiller", "0.0.0", "mb@satyarth.me")

if __name__ == '__main__':
    import_artist(sys.argv[1])