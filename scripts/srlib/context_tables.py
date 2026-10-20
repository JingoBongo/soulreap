"""The vocabulary of the context question: evidence markers and the lookup tables.

Everything that says what a package, container image, configuration-key stem or
key suffix MEANS lives here as plain data, together with the `via` markers an
edge can carry and the `_evidence` record constructor every detector builds on.
The detectors that read manifests, configuration and compose files live in
context_manifests.py and context_config.py; context.py ties them together.
"""

from __future__ import annotations

# The five markers an edge can carry. `import` and `literal` are produced in
# diagrams.py from the parsed source; the three below are produced here.
VIA_MANIFEST = "manifest"
VIA_CONFIG = "config"
VIA_COMPOSE = "compose"
VIA_IMPORT = "import"
VIA_LITERAL = "literal"
VIA_BINARY = "binary"

GENERIC_SQL = ("SQL database", "database")

# Distribution name -> the system on the other end of it. Deliberately separate
# from diagrams.SERVICE_BY_MODULE: that one is keyed on import names, and the two
# disagree often enough to matter (`psycopg2-binary` imports as `psycopg2`,
# `@aws-sdk/client-s3` imports as anything at all). Entries are matched exactly
# after normalisation; a namespace that always means one system is a prefix
# instead, listed in SYSTEM_BY_PACKAGE_PREFIX.
SYSTEM_BY_PACKAGE: dict[str, tuple[str, str]] = {
    # python
    "asyncpg": ("PostgreSQL", "database"),
    "psycopg": ("PostgreSQL", "database"),
    "psycopg2": ("PostgreSQL", "database"),
    "psycopg2-binary": ("PostgreSQL", "database"),
    "supabase": ("Supabase", "database"),
    "pymongo": ("MongoDB", "database"),
    "motor": ("MongoDB", "database"),
    "mysqlclient": ("MySQL", "database"),
    "pymysql": ("MySQL", "database"),
    "aiomysql": ("MySQL", "database"),
    "qdrant-client": ("Qdrant", "vector store"),
    "pinecone": ("Pinecone", "vector store"),
    "pinecone-client": ("Pinecone", "vector store"),
    "redis": ("Redis", "cache"),
    "aioredis": ("Redis", "cache"),
    "aiosmtplib": ("SMTP", "email"),
    "pika": ("RabbitMQ", "queue"),
    "aio-pika": ("RabbitMQ", "queue"),
    "kombu": ("RabbitMQ", "queue"),
    "kafka-python": ("Kafka", "queue"),
    "aiokafka": ("Kafka", "queue"),
    "confluent-kafka": ("Kafka", "queue"),
    "celery": ("Celery", "queue"),
    "elasticsearch": ("Elasticsearch", "search"),
    "opensearch-py": ("OpenSearch", "search"),
    "mistralai": ("Mistral", "LLM API"),
    "openai": ("OpenAI", "LLM API"),
    "anthropic": ("Anthropic", "LLM API"),
    "stripe": ("Stripe", "payments"),
    "boto3": ("AWS", "cloud"),
    "botocore": ("AWS", "cloud"),
    "aioboto3": ("AWS", "cloud"),
    "sentry-sdk": ("Sentry", "observability"),
    # Not a service on the network — the machine underneath. fuse_core reads cpu,
    # memory, disk and the process table through it, which is half of what it does.
    "psutil": ("host OS", "processes and metrics"),
    "twilio": ("Twilio", "messaging"),
    "sendgrid": ("SendGrid", "email"),
    "google-cloud-storage": ("Google Cloud Storage", "object storage"),
    "google-cloud-tasks": ("Google Cloud Tasks", "queue"),
    "google-cloud-pubsub": ("Google Pub/Sub", "queue"),
    "google-cloud-secret-manager": ("Secret Manager", "secrets"),
    "google-cloud-bigquery": ("BigQuery", "database"),
    "google-cloud-firestore": ("Firestore", "database"),
    "google-cloud-logging": ("Cloud Logging", "observability"),
    # javascript
    "pg": ("PostgreSQL", "database"),
    "postgres": ("PostgreSQL", "database"),
    "mongodb": ("MongoDB", "database"),
    "mongoose": ("MongoDB", "database"),
    "ioredis": ("Redis", "cache"),
    "mysql": ("MySQL", "database"),
    "mysql2": ("MySQL", "database"),
    "nodemailer": ("SMTP", "email"),
    "amqplib": ("RabbitMQ", "queue"),
    "kafkajs": ("Kafka", "queue"),
    "aws-sdk": ("AWS", "cloud"),
    # go
    "github.com/lib/pq": ("PostgreSQL", "database"),
    "go.mongodb.org/mongo-driver": ("MongoDB", "database"),
    # rust
    "tokio-postgres": ("PostgreSQL", "database"),
    "mongodb-rs": ("MongoDB", "database"),
    "rusoto_core": ("AWS", "cloud"),
    # ruby
    "mongoid": ("MongoDB", "database"),
    # generic last: a specific driver in the same manifest wins, see
    # diagrams.context_mermaid, which drops the generic node when one is present.
    "sqlalchemy": GENERIC_SQL,
    "sqlmodel": GENERIC_SQL,
    "sqlx": GENERIC_SQL,
    "diesel": GENERIC_SQL,
    "knex": GENERIC_SQL,
    "typeorm": GENERIC_SQL,
    "prisma": GENERIC_SQL,
    "@prisma/client": GENERIC_SQL,
}

# A namespace whose every member means the same system. Matched on the longest
# prefix so `@aws-sdk/client-dynamodb` and `github.com/jackc/pgx/v5` both land.
SYSTEM_BY_PACKAGE_PREFIX: tuple[tuple[str, tuple[str, str]], ...] = (
    ("@supabase/", ("Supabase", "database")),
    ("@aws-sdk/", ("AWS", "cloud")),
    ("@anthropic-ai/", ("Anthropic", "LLM API")),
    ("@sentry/", ("Sentry", "observability")),
    ("@elastic/", ("Elasticsearch", "search")),
    ("@google-cloud/storage", ("Google Cloud Storage", "object storage")),
    ("@google-cloud/pubsub", ("Google Pub/Sub", "queue")),
    ("@google-cloud/tasks", ("Google Cloud Tasks", "queue")),
    ("@google-cloud/bigquery", ("BigQuery", "database")),
    ("github.com/jackc/pgx", ("PostgreSQL", "database")),
    ("github.com/redis/go-redis", ("Redis", "cache")),
    ("github.com/go-redis/redis", ("Redis", "cache")),
    ("github.com/aws/aws-sdk-go", ("AWS", "cloud")),
    ("github.com/stripe/stripe-go", ("Stripe", "payments")),
    ("cloud.google.com/go/storage", ("Google Cloud Storage", "object storage")),
    ("cloud.google.com/go/pubsub", ("Google Pub/Sub", "queue")),
    ("aws-sdk-", ("AWS", "cloud")),
)

# A container image -> the system it runs. Keyed on the image's last path segment,
# so `postgres:16` and `public.ecr.aws/docker/library/postgres:16` are one entry.
SYSTEM_BY_IMAGE: dict[str, tuple[str, str]] = {
    "postgres": ("PostgreSQL", "database"),
    "postgis": ("PostgreSQL", "database"),
    "pgbouncer": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"),
    "mariadb": ("MySQL", "database"),
    "redis": ("Redis", "cache"),
    "valkey": ("Redis", "cache"),
    "memcached": ("Memcached", "cache"),
    "mongo": ("MongoDB", "database"),
    "mongodb": ("MongoDB", "database"),
    "rabbitmq": ("RabbitMQ", "queue"),
    "kafka": ("Kafka", "queue"),
    "cp-kafka": ("Kafka", "queue"),
    "redpanda": ("Kafka", "queue"),
    "zookeeper": ("ZooKeeper", "coordination"),
    "cp-zookeeper": ("ZooKeeper", "coordination"),
    "elasticsearch": ("Elasticsearch", "search"),
    "opensearch": ("OpenSearch", "search"),
    "minio": ("MinIO", "object storage"),
    "localstack": ("AWS", "cloud"),
    "keycloak": ("Keycloak", "identity provider"),
    "qdrant": ("Qdrant", "vector store"),
    "clickhouse-server": ("ClickHouse", "database"),
    "mailhog": ("SMTP", "email"),
    "mailpit": ("SMTP", "email"),
    "prometheus": ("Prometheus", "observability"),
    "grafana": ("Grafana", "observability"),
    "jaeger": ("Jaeger", "observability"),
}

# Images that are a runtime or a front door for this repository's own code, not a
# system it talks to. nvm-forecast's `web` service is `node:22-alpine` with
# ./frontend bind-mounted into it: drawing "Node" as an external system would be
# the same class of lie as "No external systems detected".
BASE_IMAGES = {
    "node", "python", "golang", "ruby", "openjdk", "eclipse-temurin", "php",
    "alpine", "ubuntu", "debian", "busybox", "nginx", "httpd", "caddy",
    "traefik", "envoy", "haproxy", "bun", "deno", "rust", "dotnet", "aspnet",
}

# The five key suffixes from the spec. A key must end in one of them with the
# underscore: `KC_HOSTNAME` names a hostname setting, not a host to call.
CONFIG_KEY_SUFFIXES = ("_URL", "_URI", "_DSN", "_ENDPOINT", "_HOST")

# What the suffix itself tells us about the system, when the stem does not name
# a system we already know.
KIND_BY_SUFFIX = {
    "_URL": "URL", "_URI": "URI", "_DSN": "database",
    "_ENDPOINT": "endpoint", "_HOST": "host",
}

# A key stem that IS the name of a system, so `APP_REDIS_URL` merges with the
# Redis the manifest already found instead of drawing a second node beside it.
# Matched on the whole stem and on its last segment, never on a fragment.
SYSTEM_BY_KEY_STEM: dict[str, tuple[str, str]] = {
    "db": GENERIC_SQL, "database": GENERIC_SQL, "sql": GENERIC_SQL,
    "postgres": ("PostgreSQL", "database"),
    "postgresql": ("PostgreSQL", "database"),
    "pg": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"),
    "mongo": ("MongoDB", "database"),
    "mongodb": ("MongoDB", "database"),
    "supabase": ("Supabase", "database"),
    "redis": ("Redis", "cache"),
    "memcached": ("Memcached", "cache"),
    "rabbitmq": ("RabbitMQ", "queue"),
    "amqp": ("RabbitMQ", "queue"),
    "kafka": ("Kafka", "queue"),
    "smtp": ("SMTP", "email"),
    "mail": ("SMTP", "email"),
    "s3": ("AWS", "cloud"),
    "aws": ("AWS", "cloud"),
    "openai": ("OpenAI", "LLM API"),
    "anthropic": ("Anthropic", "LLM API"),
    "mistral": ("Mistral", "LLM API"),
    "stripe": ("Stripe", "payments"),
    "keycloak": ("Keycloak", "identity provider"),
    "qdrant": ("Qdrant", "vector store"),
    "elasticsearch": ("Elasticsearch", "search"),
    "sentry": ("Sentry", "observability"),
}


def _evidence(system: str, kind: str, via: str, where: str, detail: str) -> dict:
    return {"system": system, "kind": kind, "via": via, "where": where, "detail": detail}
