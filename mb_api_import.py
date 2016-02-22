#/usr/bin/python3

import musicbrainzngs
import sqlite3, sys, requests
import arrow
from multiprocessing import Pool
from multiprocessing.dummy import Pool as ThreadPool
import import_tools

album_art_base_url = 'http://coverartarchive.org/release-group/'

def query_db(db, query, args=(), one=False):
    """Queries the database and returns a list of dictionaries."""
    cur = db.execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def detect_collision(slug_candidate, cursor, table):
    cursor.execute('select count(*) from {} where slug=?'.format(table), (slug_candidate,))
    if cursor.fetchall()[0][0] > 0:
        return True
    return False

def avoid_collison(slug_candidate, cursor, table):
    if not detect_collision(slug_candidate, cursor, table):
        return slug_candidate

    i = 1
    while True:
        if not detect_collision(slug_candidate + "-" + str(i), cursor, table):
            return slug_candidate + "-" + str(i)
        i += 1

def generate_slug(text, cursor, table):
    slug_candidate = import_tools.slugify(text)
    return avoid_collison(slug_candidate, cursor, table)
    
def get_canonical_url(url):
    return requests.get(url).url

def get_album_art_urls(release_group_id):
    print("Getting album art for release group " + release_group_id + "...")
    r = requests.get(album_art_base_url + release_group_id + '/')
    try:
        return (get_canonical_url(url) for url in
                (r.json()['images'][0]['image'],
                 r.json()['images'][0]['thumbnails']['large']))
    except ValueError:
        return None, None

def get_releases(mbid, processed_release_mbids):
    print("Querying MB for release groups...")
    result = musicbrainzngs.get_artist_by_id(mbid, includes=['release-groups']) 
    release_groups = result['artist']['release-group-list']
    releases = []
    for group in release_groups:
        print("Querying MB for release group " + group['id'] + "...")
        result = musicbrainzngs.get_release_group_by_id(group['id'], includes=['releases'])
        # Gets the oldest release of the group. If it fails, ignore this release group
        release_candidates = list(filter(lambda x: 'date' in x, result['release-group']['release-list']))
        if not release_candidates:
            continue
        release = min(release_candidates,
                      key=lambda release: arrow.get(release['date']+"-01-01").timestamp \
                      if len(release['date']) == 4 \
                      else arrow.get(release['date']).timestamp)

        if release['id'] in processed_release_mbids:
            print("Release " + release['id'] + " has already been processed")
            continue
        release['group-id'] = group['id']
        try:
            release['type'] = group['type']
        except KeyError:
            release['type'] = 'Unspecified'

        releases.append(release)
    return releases

def get_release(release):
    release['full-art-url'], release['thumb-art-url'] \
        = get_album_art_urls(release['group-id'])

    if release['thumb-art-url']:
        release['palette'] = import_tools.get_palette(release['thumb-art-url'])
    else:
        release['palette'] = [None, None, None]
    print("Getting deets for release " + release['id'] + "...")
    result = musicbrainzngs.get_release_by_id(release['id'], includes=['recordings', 'artists'])
    release['tracks'] = result['release']['medium-list'][0]['track-list']
    release['artists'] = result['release']['artist-credit']

def import_artist(artist_name):
    print("Querying MB for artist info...")
    result = musicbrainzngs.search_artists(artist=artist_name)
    artist_info = result['artist-list'][0]

    db = sqlite3.connect("sample.db")
    cursor = db.cursor()

    # Check if the artist's MBID matches the 'incomplete' field of any other artists
    # If so, get the artist_id and set the 'incomplete' field to NULL
    # If not, import as a new artist into the database
    cursor.execute('select id from artists where incomplete=?', (artist_info['id'],))
    result = cursor.fetchall()
    try:
        (artist_id,) = result[0]
        cursor.execute('select release_id from authorships where artist_id=?', (artist_id,))
        processed_release_ids = [_id for (_id,) in cursor.fetchall()]
        processed_release_mbids = [mbid for (mbid,) in [query_db(db,'select mbid from release_mbid where release_id=?', (release_id,), True)\
                                   for release_id in processed_release_ids]]
        cursor.execute("update artists set incomplete = NULL where id=?", (artist_id,))

    except IndexError:
        cursor.execute(
            "insert into artists (name, slug, incomplete) values (?, ?, ?)",
            (artist_info["name"], generate_slug(artist_info["name"], cursor, 'artists'), None)
        )

        artist_id = cursor.lastrowid
        processed_release_mbids = []

        print("Getting description from wikipedia...")
        cursor.execute("insert into artist_descriptions (artist_id, description) values (?, ?)", (artist_id, import_tools.get_description(artist_info['name'])))

    incomplete_artist_mbids = {mbid: artist_id for (mbid, artist_id,) in query_db(db,'select incomplete, id from artists where incomplete is not null')}
    
    pool = ThreadPool(8)
    releases = get_releases(artist_info['id'], processed_release_mbids)
    pool.map(get_release, releases)

    # Dictionary of artist MBIDs to local IDs which have already been processed and can't make dummy entries in the artists table
    processed_artist_mbids = {artist_info['id']: artist_id}

    for release in releases:
        cursor.execute(
            "insert into releases (title, slug, date, type, full_art_url, thumb_art_url) values (?, ?, ?, ?, ?, ?)",
            (release['title'],
             generate_slug(release['title'], cursor, 'releases'),
             release['date'],
             release['type'],
             release['full-art-url'],
             release['thumb-art-url'])
        )
        release['local-id'] = cursor.lastrowid

        cursor.execute(
            "insert into release_colors (release_id, color1, color2, color3) values (?, ?, ?, ?)",
            (release['local-id'],
             release['palette'][0],
             release['palette'][1],
             release['palette'][2])
        )

        cursor.execute(
            "insert into release_mbid (release_id, mbid) values (?, ?)",
            (release['local-id'],
             release['id'])
        )

        for artist in release['artists']:
            try:
                if artist['artist']['id'] in processed_artist_mbids:
                    artist['artist']['local-id'] = processed_artist_mbids[artist['artist']['id']]
                elif artist['artist']['id'] in incomplete_artist_mbids:
                    artist['artist']['local-id'] = incomplete_artist_mbids[artist['artist']['id']]
                else:
                    # Make a dummy entry into the artists table
                    cursor.execute(
                        "insert into artists (name, slug, incomplete) values (?, ?, ?)",
                        (artist['artist']['name'],
                         generate_slug(artist['artist']['name'], cursor, 'artists'),
                         artist['artist']['id'])
                    )
                    artist['artist']['local-id'] = cursor.lastrowid
                    processed_artist_mbids[artist['artist']['id']] = artist['artist']['local-id']


                    cursor.execute(
                        "insert into artist_descriptions (artist_id, description) values (?, ?)",
                        (artist['artist']['local-id'], import_tools.get_description(artist['artist']['name']))
                    )

                cursor.execute(
                    "insert into authorships (release_id, artist_id) values (?, ?)",
                    (release['local-id'],
                     artist['artist']['local-id'])
                )

            except TypeError:
                pass

        for track in release['tracks']:
            try:
                length = track['recording']['length']
            except KeyError:
                length = None
            cursor.execute(
                "insert into tracks (release_id, title, slug, position, runtime) values (?, ?, ?, ?, ?)",
                (release['local-id'],
                 track['recording']['title'],
                 generate_slug(track['recording']['title'], cursor, 'tracks'),
                 int(track['position']),
                 length)
            )

    db.commit()

musicbrainzngs.set_useragent("Skiller", "0.0.0", "mb@satyarth.me")

if __name__ == '__main__':
    import_artist(sys.argv[1])
