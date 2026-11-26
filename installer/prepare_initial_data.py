"""Prepare an immutable seed dataset and a path-relocated SQLite index.

This helper only reads the source database (through SQLite's Backup API) and
source PDFs. All writes are confined to a new staging directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any
from uuid import uuid4


PATH_COLUMNS = {
    ("documents", "path"),
    ("documents", "duplicate_of"),
    ("pages", "document_path"),
    ("processing_attempts", "document_path"),
}


class PackageValidationError(RuntimeError):
    """Raised when the seed payload cannot be proven consistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _copy_sqlite_backup(source_path: Path, destination_path: Path) -> dict[str, Any]:
    """Copy one SQLite database consistently and return verifiable metadata."""
    source_path = source_path.resolve(strict=True)
    source_hash_before = sha256_file(source_path)
    source = _sqlite_read_only(source_path)
    try:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode = DELETE")
            integrity = [row[0] for row in destination.execute("PRAGMA integrity_check")]
            foreign_keys = list(destination.execute("PRAGMA foreign_key_check"))
            tables = _tables(destination)
            table_rows = {
                table: int(destination.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in tables
            }
        finally:
            destination.close()
    finally:
        source.close()

    source_hash_after = sha256_file(source_path)
    if source_hash_before != source_hash_after:
        raise PackageValidationError(f"O banco de origem mudou durante a cópia: {source_path}")
    if integrity != ["ok"] or foreign_keys:
        raise PackageValidationError(
            f"A cópia SQLite falhou na validação: {source_path}; integrity={integrity}; foreign_keys={foreign_keys[:5]}"
        )
    return {
        "file": source_path.name,
        "source_sha256": source_hash_before,
        "staged_sha256": sha256_file(destination_path),
        "integrity_check": "ok",
        "foreign_key_check": "ok",
        "table_rows": table_rows,
    }


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def _snapshot(connection: sqlite3.Connection, path_map: dict[str, str] | None = None) -> str:
    path_map = path_map or {}
    data: list[Any] = []
    for table in _tables(connection):
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows = []
        for raw in connection.execute(f'SELECT * FROM "{table}"'):
            values = []
            for column, value in zip(columns, raw):
                if (table, column) in PATH_COLUMNS and value is not None:
                    value = path_map.get(_path_key(str(value)), str(value))
                if isinstance(value, bytes):
                    value = {"blob_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
                values.append(value)
            rows.append(values)
        rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
        data.append({"table": table, "columns": columns, "rows": rows})
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _document_map(
    connection: sqlite3.Connection,
    archive_root: Path,
    target_archive: Path,
) -> tuple[dict[str, str], list[dict[str, Any]], int]:
    required = {"documents", "pages", "processing_attempts"}
    if not required.issubset(_tables(connection)):
        raise PackageValidationError("O banco não contém todas as tabelas necessárias ao índice inicial.")
    doc_columns = {row[1] for row in connection.execute('PRAGMA table_info("documents")')}
    if not {"path", "name", "indexed_sha256", "sha256", "page_count", "status", "duplicate_of"}.issubset(doc_columns):
        raise PackageValidationError("O esquema de documents não contém os campos esperados.")

    archive_root = archive_root.resolve(strict=True)
    target_archive = target_archive.resolve()
    entries: list[dict[str, Any]] = []
    path_map: dict[str, str] = {}
    for row in connection.execute(
        "SELECT path, name, indexed_sha256, sha256, page_count, status, duplicate_of FROM documents ORDER BY path"
    ):
        source_path = Path(row["path"]).resolve(strict=True)
        try:
            relative = source_path.relative_to(archive_root)
        except ValueError as error:
            raise PackageValidationError(f"Documento indexado fora do acervo autorizado: {row['name']}") from error
        if source_path.suffix.lower() != ".pdf" or source_path.name != row["name"]:
            raise PackageValidationError(f"Nome/extensão inconsistente no índice: {row['name']}")
        if not source_path.is_file():
            raise PackageValidationError(f"PDF indexado não encontrado: {row['name']}")
        if row["status"] != "indexed":
            raise PackageValidationError(f"Documento não está indexado: {row['name']} ({row['status']})")
        indexed_hash = (row["indexed_sha256"] or row["sha256"] or "").lower()
        source_hash = sha256_file(source_path)
        if len(indexed_hash) != 64 or source_hash != indexed_hash:
            raise PackageValidationError(f"Hash do PDF diverge do índice: {row['name']}")
        destination = target_archive / relative
        key = _path_key(source_path)
        if key in path_map:
            raise PackageValidationError(f"Caminho duplicado no índice: {row['name']}")
        path_map[key] = str(destination)
        entries.append({
            "source": source_path,
            "relative": relative,
            "target_path": destination,
            "sha256": source_hash,
            "page_count": int(row["page_count"]),
            "duplicate_of": row["duplicate_of"],
        })

    source_pdfs = {
        _path_key(path.resolve()): path.resolve()
        for path in archive_root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
    }
    indexed_paths = set(path_map)
    if source_pdfs.keys() != indexed_paths:
        missing = sorted(Path(value).name for value in indexed_paths - source_pdfs.keys())
        unindexed = sorted(path.name for key, path in source_pdfs.items() if key not in indexed_paths)
        raise PackageValidationError(f"PDFs e índice não correspondem. Ausentes={missing}; sem índice={unindexed}")

    for entry in entries:
        duplicate = entry["duplicate_of"]
        if duplicate is not None and _path_key(str(duplicate)) not in path_map:
            raise PackageValidationError(f"duplicate_of referencia documento fora do conjunto: {duplicate}")
    page_count = int(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
    expected_rows = sum(entry["page_count"] for entry in entries)
    if page_count != expected_rows:
        raise PackageValidationError(f"Páginas no índice ({page_count}) divergem da soma declarada ({expected_rows}).")
    page_paths = {_path_key(row[0]) for row in connection.execute("SELECT DISTINCT document_path FROM pages")}
    if not page_paths.issubset(indexed_paths):
        raise PackageValidationError("A tabela pages contém referências a documentos fora do pacote.")
    attempt_paths = {_path_key(row[0]) for row in connection.execute("SELECT DISTINCT document_path FROM processing_attempts")}
    if not attempt_paths.issubset(indexed_paths):
        raise PackageValidationError("processing_attempts contém referências a documentos fora do pacote.")
    return path_map, entries, page_count


def _rebase_database(database: Path, path_map: dict[str, str]) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        # Retrieve original path values to retain exact SQL matches while the
        # lookup map itself remains normalized for Windows path comparison.
        originals = list(connection.execute("SELECT path, duplicate_of FROM documents"))
        exact_map = {str(path): path_map[_path_key(str(path))] for path, _duplicate in originals}
        for path, duplicate in originals:
            old = str(path)
            new = exact_map[old]
            connection.execute("UPDATE pages SET document_path=? WHERE document_path=?", (new, old))
            connection.execute("UPDATE processing_attempts SET document_path=? WHERE document_path=?", (new, old))
            if duplicate is not None:
                duplicate_new = path_map.get(_path_key(str(duplicate)))
                if duplicate_new is None:
                    raise PackageValidationError(f"duplicate_of sem caminho de destino: {duplicate}")
                connection.execute("UPDATE documents SET duplicate_of=? WHERE path=?", (duplicate_new, old))
            connection.execute("UPDATE documents SET path=? WHERE path=?", (new, old))
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        failures = list(connection.execute("PRAGMA foreign_key_check"))
        if failures:
            raise PackageValidationError(f"Falha de chaves estrangeiras após remapeamento: {failures[:5]}")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise PackageValidationError(f"PRAGMA integrity_check falhou: {integrity}")
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def prepare_package(
    database: Path,
    archive_root: Path,
    output: Path,
    programdata_root: Path,
    expected_documents: int | None = None,
    expected_pages: int | None = None,
    certificate_database: Path | None = None,
) -> dict[str, Any]:
    database = database.resolve(strict=True)
    archive_root = archive_root.resolve(strict=True)
    output = output.resolve()
    programdata_root = programdata_root.resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"A pasta de staging já existe; nada será sobrescrito: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_db_hash_before = sha256_file(database)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-{uuid4().hex}-", dir=output.parent))
    try:
        payload = work / "CASSI"
        payload_archive = payload / "Acervos"
        payload_archive.mkdir(parents=True)
        target_archive = (programdata_root / "CASSI" / "Acervos").resolve()
        staged_db = payload / "cassi.db"

        main_database_metadata = _copy_sqlite_backup(database, staged_db)
        staged_connection = sqlite3.connect(staged_db)
        try:
            journal_mode = staged_connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            if str(journal_mode).lower() != "delete":
                raise PackageValidationError(f"Não foi possível tornar o journal do staging autocontido: {journal_mode}")
        finally:
            staged_connection.close()
        main_database_metadata["staged_sha256"] = sha256_file(staged_db)
        source_db_hash_after_backup = sha256_file(database)
        if certificate_database is None:
            sibling_certificate_database = database.with_name("certificate_analysis.db")
            certificate_database = sibling_certificate_database if sibling_certificate_database.is_file() else None
        certificate_database_metadata = None
        if certificate_database is not None:
            certificate_database = certificate_database.resolve(strict=True)
            certificate_database_metadata = _copy_sqlite_backup(
                certificate_database, payload / "certificate_analysis.db"
            )
        if source_db_hash_before != source_db_hash_after_backup:
            raise PackageValidationError("O hash do banco de origem mudou durante a cópia; staging cancelado.")

        copied = sqlite3.connect(staged_db)
        copied.row_factory = sqlite3.Row
        try:
            path_map, entries, pages_count = _document_map(copied, archive_root, target_archive)
            before_snapshot = _snapshot(copied, path_map)
            if expected_documents is not None and len(entries) != expected_documents:
                raise PackageValidationError(f"Esperados {expected_documents} documentos; encontrados {len(entries)}.")
            if expected_pages is not None and pages_count != expected_pages:
                raise PackageValidationError(f"Esperadas {expected_pages} páginas; encontradas {pages_count}.")
        finally:
            copied.close()

        for entry in entries:
            entry["payload_path"] = payload_archive / entry["relative"]
            entry["payload_path"].parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["source"], entry["payload_path"])
            if sha256_file(entry["payload_path"]) != entry["sha256"]:
                raise PackageValidationError(f"Hash divergiu após copiar o PDF: {entry['source'].name}")

        expected_path_map = {_path_key(entry["source"]): str(entry["target_path"]) for entry in entries}
        _rebase_database(staged_db, path_map)

        final = sqlite3.connect(staged_db)
        final.row_factory = sqlite3.Row
        try:
            after_snapshot = _snapshot(final, expected_path_map)
            if before_snapshot != after_snapshot:
                raise PackageValidationError("Dados indexados além dos caminhos foram alterados no banco de staging.")
            docs_count = int(final.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            final_pages = int(final.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
            if docs_count != len(entries) or final_pages != pages_count:
                raise PackageValidationError("As contagens do índice mudaram após o remapeamento.")
            if list(final.execute("PRAGMA integrity_check"))[0][0] != "ok":
                raise PackageValidationError("Banco final reprovado em integrity_check.")
            if list(final.execute("PRAGMA foreign_key_check")):
                raise PackageValidationError("Banco final contém referências estrangeiras inválidas.")
            rows = list(final.execute("SELECT path, page_count FROM documents ORDER BY path"))
            packaged_pdf_paths = {
                _path_key(entry["target_path"]): entry["payload_path"] for entry in entries
            }
            for row in rows:
                packaged_path = packaged_pdf_paths.get(_path_key(row["path"]))
                if packaged_path is None or not packaged_path.is_file():
                    raise PackageValidationError(f"O caminho remapeado não está associado a um PDF do pacote: {row['path']}")
                page_numbers = [int(r[0]) for r in final.execute(
                    "SELECT page_number FROM pages WHERE document_path=? ORDER BY page_number", (row["path"],)
                )]
                if page_numbers != list(range(1, int(row["page_count"]) + 1)):
                    raise PackageValidationError(f"Numeração de páginas inválida no índice: {Path(row['path']).name}")
        finally:
            final.close()

        pdf_manifest = [
            {"name": entry["relative"].as_posix(), "bytes": entry["payload_path"].stat().st_size, "sha256": entry["sha256"]}
            for entry in sorted(entries, key=lambda item: item["relative"].as_posix().casefold())
        ]
        manifest = {
            "format": 1,
            "data_root": "%PROGRAMDATA%/CASSI",
            "source_programdata_root": str(programdata_root),
            "database": {
                "file": "cassi.db",
                "source_sha256": main_database_metadata["source_sha256"],
                "staged_sha256": sha256_file(staged_db),
                "documents": len(entries),
                "pages": pages_count,
                "preserved_data_fingerprint": before_snapshot,
                "integrity_check": "ok",
                "foreign_key_check": "ok",
            },
            "pdfs": pdf_manifest,
        }
        if certificate_database_metadata is not None:
            manifest["certificate_analysis_database"] = certificate_database_metadata
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        (work / "manifest.json").write_text(manifest_text, encoding="utf-8")
        (payload / "initial-data-manifest.json").write_text(manifest_text, encoding="utf-8")
        try:
            work.rename(output)
        except FileExistsError as error:
            raise FileExistsError(f"A pasta de staging surgiu durante a preparação; nada foi sobrescrito: {output}") from error
        return manifest
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--programdata-root", type=Path, required=True)
    parser.add_argument("--expected-documents", type=int)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--certificate-database", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        manifest = prepare_package(
            args.database, args.archive_root, args.output, args.programdata_root,
            args.expected_documents, args.expected_pages, args.certificate_database,
        )
    except Exception as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "output": str(args.output.resolve()),
        "documents": manifest["database"]["documents"],
        "pages": manifest["database"]["pages"],
        "database_integrity": manifest["database"]["integrity_check"],
        "foreign_key_check": manifest["database"]["foreign_key_check"],
        "pdf_count": len(manifest["pdfs"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
