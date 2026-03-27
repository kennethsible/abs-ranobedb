# RanobeDB Metadata Provider for Audiobookshelf

[RanobeDB](https://ranobedb.org/) is a database for Japanese light novels and any official translations. The current Audiobookshelf metadata providers frequently struggle with light novels. For example, Google Books often incorrectly identifies light novels as manga, assigns overly generic genres (e.g., "light novel" as the sole genre tag), and fails to recognize volume numbers as parts of a series.

## Run with Docker CLI

```bash
docker run -d --name abs-ranobedb -p 5000:5000 ghcr.io/kennethsible/abs-ranobedb
```

## Run with Docker Compose

```docker
services:
  abs-ranobedb:
    image: ghcr.io/kennethsible/abs-ranobedb
    container_name: abs-ranobedb
    environment:
      LOG_LEVEL: "INFO"
      SEARCH_LIMIT: 5
      PREFER_ROMAJI: "true"
      AMAZON_COVERS: "false"
    volumes:
      - ranobedb_cache:/app/cache
    ports:
      - "5000:5000"
    restart: unless-stopped

volumes:
  ranobedb_cache:
```

| Variable | Default | Description |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Logging verbosity level (e.g., `DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SEARCH_LIMIT` | `5` | Maximum number of search results to retrieve from RanobeDB |
| `PREFER_ROMAJI` | `true` | If `true`, romanization is preferred for original Japanese releases |
| `AMAZON_COVERS` | `false` | If `true`, larger cover images are fetched from Amazon via ASIN |

> [!NOTE]
> This project is not affiliated with RanobeDB, but it is built to strictly respect their API guidelines. To ensure responsible usage, `abs-ranobedb` has an internal rate limiter (max 60 requests per minute), utilizes a persistent Docker volume to cache redundant API calls, and allows users to limit the number of search results using an environment variable.

## Install with Python (No Docker)

```bash
pip install git+https://github.com/kennethsible/abs-ranobedb.git
```

## Configure Audiobookshelf

```text
Settings -> Item Metadata Utils -> Custom Metadata Providers -> Add
```

- **Name**: RanobeDB
- **URL**: [http://abs-ranobedb:5000](http://abs-ranobedb:5000)
- **Authorization Header Value**: None

> [!IMPORTANT]
> If Audiobookshelf is running on the host, use `localhost` or a local IP address in the URL field instead of the container name.
