CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('requester','coordinator','approver'))
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY,
 owner_id INTEGER NOT NULL REFERENCES users(id),
 title TEXT NOT NULL,
 organizer TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('club','faculty')),
 description TEXT NOT NULL,
 date TEXT NOT NULL,
 start TEXT NOT NULL,
 end TEXT NOT NULL CHECK(end > start),
 venue TEXT NOT NULL,
 attendees INTEGER NOT NULL CHECK(attendees BETWEEN 1 AND 10000),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','reviewed','approved','changes','rejected')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS event_reservations ON events(status,date,venue,start,end);
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY,
 event_id INTEGER NOT NULL REFERENCES events(id),
 actor_id INTEGER NOT NULL REFERENCES users(id),
 action TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS revisions (
 id INTEGER PRIMARY KEY,
 event_id INTEGER NOT NULL REFERENCES events(id),
 actor_id INTEGER NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL,
 proposal TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(event_id, version)
);
CREATE INDEX IF NOT EXISTS history_event ON history(event_id, id);
