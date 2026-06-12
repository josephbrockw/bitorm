  1. Bulk insert + transactions. Right now every save() is its own INSERT + commit. I benchmarked it: 5,000 rows via a
  save() loop took 0.94s (~5,300 rows/s); the same rows via executemany in one transaction took 0.002s (~2.8M rows/s).
  That's a ~525x difference, and it gets worse on slower disks. Loading a 500k-sample dataset would take minutes instead
  of a fraction of a second. The db.atomic() context manager already on your roadmap solves half of this; I'd pair it
  with a Model.bulk_create(objs) that validates, then uses executemany in a single transaction. For ML ingest, this is
  the single highest-impact feature you could add.

  2. A JSON field. The README says unsupported types "pass through to SQLite as-is," but they actually don't — I tested
  it: Run(name="exp1", metrics={"loss": 0.3}).save() raises ProgrammingError: type 'dict' is not supported. Storing
  hyperparameters, metrics dicts, and config blobs is the bread and butter of experiment tracking. A JSONField that does
  json.dumps/json.loads in to_db/from_db is maybe 20 lines given your existing _Column machinery. At minimum, fix the
  README claim.

  3. Concurrency story (WAL mode + threads). Two problems compound here: sqlite3.connect() defaults to
  check_same_thread=True, and there's a single module-global connection. A PyTorch DataLoader with num_workers > 0, or a
  training loop writing metrics while you query progress from another process, will hit "objects created in a thread
  can only be used in that same thread" or database is locked. Recommendations: set PRAGMA journal_mode=WAL and a
  busy_timeout in Database.__init__ (two lines, huge practical win for concurrent readers + one writer), and document
  the threading limitation — or go further with thread-local connections.

 
