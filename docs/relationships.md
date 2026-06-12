# Relationships

[← Back to README](../README.md) · [Models & Fields](fields.md) · [Querying](querying.md) · [Migrations](migrations.md) · [Transactions & Concurrency](concurrency.md)

## Foreign Keys

```python
class Author(Model):
    name: str

class Post(Model):
    title: str
    author: Author = ForeignKey(Author)
```

This creates an `author_id` INTEGER column on the `post` table with a foreign key reference to `author(id)`.

### Forward Access

```python
post = Post.get(id=1)
post.author        # lazily fetches and caches the Author object
post.author_id     # the raw FK value (integer)
```

### Setting Foreign Keys

```python
author = Author(name="Ada")
author.save()                          # must be saved first
post = Post(title="Hello", author=author)
post.save()

post.author = other_author             # reassign (must be saved)
post.author = None                     # clear the relationship
```

Assigning an unsaved object raises `ValueError`.

### Reverse Access

The target model gets a reverse accessor that returns a QuerySet:

```python
author.posts.count()                   # default name: <model>s
author.posts.filter(title__like="%x%")
list(author.posts)
```

### Options

```python
ForeignKey(
    Author,
    related_name="written_posts",  # custom reverse accessor name
    nullable=True,                 # allow NULL (default)
    on_delete="CASCADE",           # CASCADE | SET NULL | RESTRICT | NO ACTION
)
```

### Filtering Across Relations

Related fields work in filters and ordering via JOINs — see
[Querying: Filtering Across Relations](querying.md#filtering-across-relations):

```python
Post.filter(author__name="Ada")
Post.all().order_by("author__name")
```

## Many-to-Many

```python
class Tag(Model):
    label: str = Field(unique=True)

class Article(Model):
    title: str
    tags: list = ManyToMany(Tag)
```

`Article.create_table()` also creates the join table (`article_tag`).

### Managing Relations

```python
article = Article(title="Hello")
article.save()
tag = Tag(label="python")
tag.save()

article.tags.add(tag)              # link
article.tags.add(tag1, tag2)       # link multiple
article.tags.remove(tag)           # unlink
article.tags.clear()               # remove all links

article.tags.all()                 # list of Tag objects
article.tags.count()               # number of linked tags
list(article.tags)                 # iterable

# reverse side:
tag.articles.all()                 # default name: <owner>s
```

All objects must be saved before calling `add()`.

Custom reverse name:

```python
tags: list = ManyToMany(Tag, related_name="tagged_articles")
```
