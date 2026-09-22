#!/usr/bin/env python3

import csv
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.yaml"
INDICES_DIR = BASE_DIR / "indices"
LOG_FILE = BASE_DIR / "logs" / "export.log"


def setup_logging():
    """Configure file and console logging."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.handlers:
        logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def load_config():
    """Load and validate the YAML configuration file."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_FILE}")

    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not config:
        raise ValueError("Configuration file is empty.")

    return config


def build_session(config):
    """Build an HTTP session using API Key or Basic authentication."""
    elastic = config["elastic"]
    authentication = elastic["authentication"]
    method = authentication["method"].lower()

    session = requests.Session()

    if method == "apikey":
        api_key = authentication["apikey"]["key"]

        if not api_key or api_key == "CHANGE_ME":
            raise ValueError("API Key is not configured.")

        session.headers.update({
            "Authorization": f"ApiKey {api_key}"
        })

    elif method == "basic":
        username = authentication["basic"]["username"]
        password = authentication["basic"]["password"]

        if not username or username == "CHANGE_ME":
            raise ValueError("Basic authentication username is not configured.")

        session.auth = (username, password)

    else:
        raise ValueError(
            f"Unsupported authentication method: {method}. "
            "Use 'apikey' or 'basic'."
        )

    return session


def elastic_request(session, config, method, url, params=None):
    """Execute an Elasticsearch HTTP request and validate the response."""
    elastic = config["elastic"]

    response = session.request(
        method=method,
        url=url,
        params=params,
        verify=elastic.get("verify_ssl", False),
        timeout=elastic.get("timeout_seconds", 60),
    )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Elasticsearch authentication/authorization failed "
            f"(HTTP {response.status_code})."
        )

    if response.status_code == 404:
        raise RuntimeError(
            f"Elasticsearch resource not found (HTTP 404): {url}"
        )

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        response_body = response.text[:500]
        raise RuntimeError(
            f"Elasticsearch request failed: {exc}. "
            f"Response: {response_body}"
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Elasticsearch returned an invalid JSON response."
        ) from exc


def get_document_count(session, config, index_name):
    """Return the document count for an Elasticsearch index."""
    server = config["elastic"]["server"].rstrip("/")
    url = f"{server}/{index_name}/_count"

    data = elastic_request(
        session,
        config,
        "GET",
        url,
        params={"filter_path": "count"},
    )

    if "count" not in data:
        raise RuntimeError(
            f"_count response for '{index_name}' does not contain 'count'."
        )

    return int(data["count"])


def get_documents(session, config, index_name, result_size):
    """Retrieve documents from an Elasticsearch index."""
    server = config["elastic"]["server"].rstrip("/")
    url = f"{server}/{index_name}/_search"

    data = elastic_request(
        session,
        config,
        "GET",
        url,
        params={
            "filter_path": "hits.hits._source",
            "size": result_size,
            "_source": "true",
        },
    )

    hits = data.get("hits", {}).get("hits", [])

    documents = []

    for hit in hits:
        source = hit.get("_source", {})

        if isinstance(source, dict):
            documents.append(source)

    return documents


def flatten_value(value):
    """Convert nested values into CSV-compatible strings."""
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return value


def write_csv(path, documents):
    """Write Elasticsearch _source documents to a CSV file."""
    if not documents:
        raise ValueError("No documents available to write to CSV.")

    fieldnames = []
    seen_fields = set()

    # Build the union of all fields found in the documents.
    for document in documents:
        for field in document.keys():
            if field not in seen_fields:
                seen_fields.add(field)
                fieldnames.append(field)

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for document in documents:
            row = {
                field: flatten_value(document.get(field))
                for field in fieldnames
            }
            writer.writerow(row)


def validate_csv(path, expected_documents):
    """Validate the generated CSV before it becomes the final export."""
    if not path.exists():
        raise RuntimeError(f"Temporary CSV was not created: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)

        try:
            header = next(reader)
        except StopIteration as exc:
            raise RuntimeError("Temporary CSV is empty.") from exc

        if not header:
            raise RuntimeError("Temporary CSV has no header.")

        rows = sum(1 for _ in reader)

    if rows != expected_documents:
        raise RuntimeError(
            f"CSV validation failed: expected {expected_documents} "
            f"data rows, found {rows}."
        )

    return rows


def rotate_temp_files(temp_dir, index_name, max_files):
    """Keep only the most recent temporary CSV files."""
    pattern = f"{index_name}_temp_*.csv"

    files = sorted(
        temp_dir.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for old_file in files[max_files:]:
        try:
            old_file.unlink()
            logging.info("Old temporary file removed: %s", old_file)
        except OSError as exc:
            logging.warning(
                "Unable to remove old temporary file %s: %s",
                old_file,
                exc,
            )


def update_final_files(index_name, temp_file):
    """Create the backup and atomically replace the final CSV."""
    index_dir = INDICES_DIR / index_name
    final_file = index_dir / f"{index_name}.csv"
    backup_file = index_dir / f"{index_name}_back.csv"

    if final_file.exists():
        shutil.copy2(final_file, backup_file)
        logging.info("Backup created: %s", backup_file)

    staging_file = index_dir / f".{index_name}.csv.new"

    try:
        shutil.copy2(temp_file, staging_file)
        staging_file.replace(final_file)
    finally:
        if staging_file.exists():
            staging_file.unlink()

    logging.info("Final CSV updated: %s", final_file)


def process_index(session, config, index_name):
    """Process one enabled Elasticsearch index."""
    logging.info("--------------------------------------------------")
    logging.info("Processing index: %s", index_name)

    index_dir = INDICES_DIR / index_name
    temp_dir = index_dir / "temp"

    index_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    size_offset = int(config["export"].get("size_offset", 10))
    temp_history = int(config["export"].get("temp_history", 5))

    document_count = get_document_count(
        session,
        config,
        index_name,
    )

    logging.info(
        "Documents in Elasticsearch: %d",
        document_count,
    )

    if document_count == 0:
        logging.warning(
            "Index '%s' contains 0 documents. "
            "Final CSV will not be modified.",
            index_name,
        )
        return

    result_size = document_count + size_offset

    logging.info(
        "Search size requested: %d (count + %d)",
        result_size,
        size_offset,
    )

    documents = get_documents(
        session,
        config,
        index_name,
        result_size,
    )

    retrieved_documents = len(documents)

    logging.info(
        "Documents retrieved: %d",
        retrieved_documents,
    )

    if retrieved_documents != document_count:
        raise RuntimeError(
            f"Document count mismatch for '{index_name}': "
            f"Elasticsearch count={document_count}, "
            f"retrieved={retrieved_documents}. "
            "Final CSV will not be modified."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_file = temp_dir / (
        f"{index_name}_temp_{timestamp}.csv"
    )

    write_csv(temp_file, documents)

    logging.info(
        "Temporary CSV created: %s",
        temp_file,
    )

    validated_rows = validate_csv(
        temp_file,
        document_count,
    )

    logging.info(
        "CSV validation successful: %d data rows",
        validated_rows,
    )

    update_final_files(
        index_name,
        temp_file,
    )

    rotate_temp_files(
        temp_dir,
        index_name,
        temp_history,
    )

    logging.info(
        "Index '%s' export completed successfully.",
        index_name,
    )


def main():
    """Run the export process for all enabled indices."""
    setup_logging()

    logging.info("==================================================")
    logging.info("Starting Elasticsearch index-to-CSV export")
    logging.info("==================================================")

    try:
        config = load_config()
        session = build_session(config)

        indices = config.get("indices", {})

        if not indices:
            raise ValueError("No indices configured.")

        enabled_indices = [
            index_name
            for index_name, enabled in indices.items()
            if bool(enabled)
        ]

        if not enabled_indices:
            logging.warning(
                "No enabled indices. Nothing to process."
            )
            return 0

        logging.info(
            "Enabled indices: %s",
            ", ".join(enabled_indices),
        )

        failures = 0

        for index_name in enabled_indices:
            try:
                process_index(
                    session,
                    config,
                    index_name,
                )
            except Exception as exc:
                failures += 1
                logging.exception(
                    "Export failed for index '%s': %s",
                    index_name,
                    exc,
                )

        if failures:
            logging.error(
                "Export completed with %d failed index(es).",
                failures,
            )
            return 1

        logging.info(
            "All exports completed successfully."
        )

        return 0

    except Exception as exc:
        logging.exception(
            "Fatal error: %s",
            exc,
        )
        return 1

    finally:
        logging.info(
            "=================================================="
        )
        logging.info(
            "End of Elasticsearch index-to-CSV export"
        )
        logging.info(
            "=================================================="
        )


if __name__ == "__main__":
    sys.exit(main())
