# elastic-index-to-csv

Copyright (c) 2026 Mikofb  
SPDX-License-Identifier: MIT

A lightweight Python utility to export Elasticsearch index documents to CSV files.

## Features

- Multi-index export engine
- API Key authentication
- Basic authentication
- YAML-based configuration
- Dynamic CSV columns based on Elasticsearch `_source` fields
- Automatic document count retrieval
- Configurable `count + offset` search size
- Timestamped temporary CSV files
- Automatic retention of temporary files
- Backup of the previous final CSV
- CSV validation before publication
- Safe final CSV replacement
- File and console logging
- Cron-friendly execution

## Project structure

The repository contains the application code and configuration examples.

`indices/` and `logs/` are **runtime directories**. They are intentionally created and populated by the application at runtime and are not required to exist in a fresh Git clone.

```text
elastic-index-to-csv/
├── config/
│   └── config.example.yaml
├── script/
│   └── export_csv.py
├── indices/                    # Runtime directory
│   └── <index-name>/
│       ├── <index-name>.csv
│       ├── <index-name>_back.csv
│       └── temp/
├── logs/                       # Runtime directory
│   └── export.log
├── .gitignore
└── README.md
```

## Requirements

- Linux
- Python 3.9+
- Network access to Elasticsearch
- An Elasticsearch account or API Key with the required read permissions

## Installation

Create a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
python3 -m pip install requests PyYAML
```

### Python / pip troubleshooting

Check the active Python/pip environment with:

```bash
python3 -m pip --version
```

Check/install the required dependencies with:

```bash
python3 -m pip install requests PyYAML
```

Using `python3 -m pip` rather than relying on a standalone `pip` command is recommended on Linux servers, especially when multiple Python installations may exist.

For a cleaner and more reproducible deployment, use a dedicated virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install requests PyYAML
```

If `venv` is not available, install the virtual-environment package provided by the Linux distribution and create the environment again.

## Configuration

Copy the example configuration:

```bash
cp config/config.example.yaml config/config.yaml
```

### API Key authentication

```yaml
elastic:
  server: "https://elastic-server:9200"

  authentication:
    method: "apikey"

    apikey:
      key: "YOUR_API_KEY"
```

The script sends:

```http
Authorization: ApiKey YOUR_API_KEY
```

### Basic authentication

```yaml
elastic:
  authentication:
    method: "basic"

    basic:
      username: "YOUR_USERNAME"
      password: "YOUR_PASSWORD"
```

### Index selection

```yaml
indices:
  index-name-1: true
  index-name-2: false
  index-name-3: false
  index-name-4: false
```

### Export settings

```yaml
export:
  size_offset: 10
  temp_history: 5
```

If `_count` returns `1532`, the script requests:

```text
size = 1532 + 10 = 1542
```

The script then verifies that exactly `1532` documents were retrieved before updating the final CSV.

## Protecting credentials

As credentials are stored in `config.yaml`:

```bash
chmod 600 config/config.yaml
```

## Runtime output

For `index-name-1`:

```text
indices/index-name-1/index-name-1.csv
indices/index-name-1/index-name-1_back.csv
indices/index-name-1/temp/index-name-1_temp_YYYYMMDD_HHMMSS.csv
```

The main CSV is intended for consumption by third-party systems.

The `_back.csv` file contains the previous version of the main CSV before the latest successful update.

The timestamped temporary files provide a short execution history. By default, only the latest five are retained.

## Processing flow

```text
Elasticsearch
      |
      +----> _count
      |          |
      |          v
      |     document count
      |
      +----> _search (count + offset)
                 |
                 v
             _source
                 |
                 v
       timestamped temporary CSV
                 |
                 v
             validation
                 |
          +------+------+
          |             |
        FAIL           OK
          |             |
          v             v
     keep current    backup current
        CSV               |
                          v
                    publish new CSV
```

The final CSV is not modified if the Elasticsearch request fails, the document count does not match, or CSV validation fails.

## Cron

Every hour:

```cron
0 * * * * /opt/elastic-index-to-csv/venv/bin/python /opt/elastic-index-to-csv/script/export_csv.py
```

Every 30 minutes:

```cron
*/30 * * * * /opt/elastic-index-to-csv/venv/bin/python /opt/elastic-index-to-csv/script/export_csv.py
```

## Logging

Logs are written to:

```text
logs/export.log
```

The log records authentication/request failures, document counts, retrieved documents, CSV validation, backups, final publication, and overall execution status.

## Security recommendations

- Prefer a dedicated Elasticsearch API Key for the exporter.
- Grant only the minimum required read permissions.
- Never commit credentials to Git.
- Keep `config.yaml` outside the public repository or use a sanitized example.
- Consider `verify_ssl: true` when a trusted Elasticsearch CA certificate is available.
- Restrict permissions on configuration and runtime directories.

## License

This project is licensed under the MIT License. See the `LICENSE` file.

## Copyright

Copyright (c) 2026 Mikofb.
