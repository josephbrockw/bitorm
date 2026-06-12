# Querying

[← Back to README](../README.md) · [Models & Fields](fields.md) · [Relationships](relationships.md) · [Migrations](migrations.md) · [Transactions & Concurrency](concurrency.md)

## Basic Queries

```python
User.all()                    # lazy QuerySet of all rows
User.get(email="ada@x.com")  # single object or None (raises if >1 match)
```

## Raw SQL

For queries the ORM can't express — GROUP BY, window functions, CTEs, complex joins:

```python
# Returns model instances (deserialized through _from_row)
users = User.raw("SELECT * FROM user WHERE age > ? ORDER BY name", (18,))

# Returns raw sqlite3.Row objects (for aggregations, cross-table queries)
db = connect("app.db")
rows = db.raw("SELECT label, COUNT(*) as cnt FROM classification GROUP BY label")
for row in rows:
    print(row["label"], row["cnt"])
```

Both methods use parameterized queries. `Model.raw()` applies the same type deserialization as normal queries (datetime, date, bool).

## Get or Create

Look up an object by filter criteria, creating it if it doesn't exist:

```python
user, created = User.get_or_create(
    email="ada@example.com",
    defaults={"name": "Ada", "age": 36},
)
# created=True  → new row was inserted
# created=False → existing row was returned
```

`**kwargs` are the lookup fields. `defaults` is an optional dict of additional fields applied only when creating — they're not used in the lookup. If `get()` finds multiple matches, `ValueError` is raised (same as calling `get()` directly).

## Filtering

Filter with Django-style keyword arguments:

```python
User.filter(age=36)                 # age = 36
User.filter(age__gt=18)             # age > 18
User.filter(age__gte=18)            # age >= 18
User.filter(age__lt=65)             # age < 65
User.filter(age__lte=65)            # age <= 65
User.filter(name__ne="Bob")         # name != 'Bob'
User.filter(name__like="%ada%")     # name LIKE '%ada%'
User.filter(id__in=[1, 2, 3])       # id IN (1, 2, 3)
User.filter(value=None)             # value IS NULL
User.filter(value__ne=None)         # value IS NOT NULL
```

## Chaining

QuerySets are lazy and immutable. Each method returns a new QuerySet:

```python
qs = User.filter(age__gte=18).order_by("-age").limit(10)
# No query runs until you evaluate the QuerySet:
results = list(qs)
```

## Ordering

```python
User.all().order_by("name")         # ASC
User.all().order_by("-name")        # DESC
User.all().order_by("age", "name")  # multiple columns
```

## Evaluation

QuerySets evaluate lazily. These operations trigger a query:

```python
list(qs)          # iterate
qs[0]             # index
qs[1:3]           # slice
len(qs)           # length
bool(qs)          # truthiness
repr(qs)          # representation
```

Other methods:

```python
qs.first()        # first result or None (adds LIMIT 1)
qs.count()        # COUNT(*) query (or len(cache) if already evaluated)
qs.delete()       # bulk DELETE matching the filters, returns row count
```

## Distinct

```python
User.all().distinct()                # SELECT DISTINCT ...
User.filter(age__gte=18).distinct().count()  # count distinct matching rows
```

`distinct()` is chainable and returns a new QuerySet. Note that `SELECT DISTINCT *` with a unique primary key has no effect since every row is already unique — this matches standard SQL behavior.

## Aggregates

`sum()`, `avg()`, `min()`, and `max()` are terminal methods that return a scalar:

```python
User.all().sum("age")               # total of all ages
User.filter(active=True).avg("age") # average age of active users
User.all().min("name")              # alphabetically first name
User.all().max("created")           # most recent datetime
```

Aggregates respect filters but ignore `order_by()` and `limit()` (which are irrelevant to aggregate results). Returns `None` when no rows match.

The column name is validated — passing a nonexistent column raises `ValueError` with a helpful message listing valid columns.

Results are deserialized through the same type conversion as regular queries, so `min("created")` on a datetime column returns a `datetime` object, not a string.

## Filtering Across Relations

Single-hop related field lookups work via JOINs (see
[Relationships](relationships.md) for defining them):

```python
Post.filter(author__name="Ada")            # JOIN + WHERE
Post.filter(author__email__like="%ada%")   # JOIN + WHERE with operator
Post.all().order_by("author__name")        # JOIN + ORDER BY
```
