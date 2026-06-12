# Transactions & Concurrency

[← Back to README](../README.md) · [Models & Fields](fields.md) · [Querying](querying.md) · [Relationships](relationships.md) · [Migrations](migrations.md)

## Transactions

By default every `save()`, `delete()`, and M2M operation commits immediately.
`db.atomic()` groups writes into a single transaction — all of them commit at the
end, or none do if an exception escapes the block:

```python
from bitorm import connect

db = connect("app.db")

with db.atomic():
    author = Author(name="Ada")
    author.save()
    post = Post(title="Hello", author=author)
    post.save()
# both committed here — or neither, if an exception occurred
```

All ORM write operations (`save`, `delete`, `QuerySet.delete`, M2M
`add`/`remove`/`clear`, `bulk_create`) participate automatically; explicit
`db.commit()` calls inside the block are no-ops.

Blocks nest using SAVEPOINTs — an inner block that fails rolls back only its own
writes:

```python
with db.atomic():
    a.save()
    try:
        with db.atomic():
            b.save()
            raise RuntimeError("inner failure")
    except RuntimeError:
        pass
# a is committed, b is not
```

Wrapping a large loop of `save()` calls in `atomic()` also makes it dramatically
faster, since there's a single commit at the end.

[Migrations](migrations.md) run atomically too: each migration file is applied in
its own transaction, so a failure mid-migration rolls back cleanly.

## Bulk Inserts

`save()` commits per row, which is slow for large batches (each commit is a disk
sync). `bulk_create()` inserts many objects in a single transaction — orders of
magnitude faster, and the right tool for loading datasets:

```python
samples = [Sample(label=lbl, score=s) for lbl, s in data]
Sample.bulk_create(samples)
samples[0].id  # generated primary keys are populated
```

All objects are validated up front and inserted all-or-nothing: if any row fails
(validation error, constraint violation), nothing is kept.

## WAL Mode and Fork Safety

File-backed databases are opened in [WAL mode](https://www.sqlite.org/wal.html)
with a 5-second busy timeout. In practice this means **multiple processes can read
while one writes** — e.g. a training loop logging metrics while another process
queries progress — and writers wait briefly for each other instead of immediately
raising `database is locked`.

Connections are also **fork-safe**: BitORM detects when it has crossed a `fork()`
and transparently opens a fresh connection in the child process.

What's *not* supported: sharing one connection across threads. Use one process (or
one `connect()` per thread) instead.

## PyTorch DataLoader

A `Dataset` that reads through BitORM works with `num_workers > 0`:

```python
from torch.utils.data import Dataset, DataLoader
from bitorm import connect

class EmailDataset(Dataset):
    def __len__(self):
        return Email.all().count()

    def __getitem__(self, idx):
        email = Email.get(id=idx + 1)
        return encode(email.raw.read_text()), email.label

connect("app.db")
loader = DataLoader(
    EmailDataset(),
    num_workers=4,
    worker_init_fn=lambda _: connect("app.db"),
)
```

With the `fork` start method (Linux default) the fork-safe reconnect makes workers
just work; the `worker_init_fn` shown above is required under the `spawn` start
method (macOS/Windows default), where workers start fresh interpreters, and is
harmless under `fork` — include it for portability.
