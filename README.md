# RanobeDB Metadata Provider for Audiobookshelf

[RanobeDB](https://ranobedb.org/) is a database for Japanese light novels and any official translations. The current Audiobookshelf metadata providers frequently struggle with light novels. For example, Google Books often incorrectly identifies light novels as manga, assigns overly generic genres (e.g., "light novel" as the sole genre tag), and fails to recognize volume numbers as parts of a series.

## Run with Docker CLI

```bash
docker run -d \
  --name abs-ranobedb \
  -p 5000:5000 \
  --restart unless-stopped \
  ghcr.io/kennethsible/abs-ranobedb
```

## Run with Docker Compose

```docker
services:
  abs-ranobedb:
    image: ghcr.io/kennethsible/abs-ranobedb
    container_name: abs-ranobedb
    environment:
      LOG_LEVEL: "INFO"
      MAX_RESULTS: 5
    ports:
      - "5000:5000"
    restart: unless-stopped
```

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
> If Audiobookshelf is also running in Docker on the same network, the above URL can use the `abs-ranobedb` container name. However, if Audiobookshelf is running on the host, the URL should use `localhost` or your local IP with a forwarded port.
