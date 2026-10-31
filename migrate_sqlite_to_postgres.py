"""Copy the SQLite database of the earlier version of this app into PostgreSQL, once.

    python migrate_sqlite_to_postgres.py                    # data/inventory.sqlite3 -> $DATABASE_URL
    python migrate_sqlite_to_postgres.py old.sqlite3 --database-url postgresql://user:pass@host/db
    python migrate_sqlite_to_postgres.py --dry-run          # check everything, write nothing

What it does:
  * opens the SQLite file read-only. The file is never changed, so it stays as your backup.
  * creates the PostgreSQL tables if they are missing, then copies every row with its original id
    in ONE transaction: either everything lands or nothing does.
  * reads every copied row back and compares it with the SQLite row before it commits.
  * refuses to touch a database that already holds data. The six demo students that a first start
    of the app adds are the one exception: they are replaced.

Stop the app first (the old SQLite version too), so nothing is written while the copy runs.
"""
import argparse
import os
import secrets
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import psycopg

from database import STATUS_RENAMES, TABLES, init_schema, only_untouched_demo_data

DEFAULT_SOURCE = Path(__file__).resolve().parent / 'data' / 'inventory.sqlite3'


class MigrationError(Exception):
    """Something the person running the script can fix. Nothing has been written."""


def clean(value, stats):
    """PostgreSQL text cannot hold NUL characters: drop them, and count the values that changed."""
    if isinstance(value, str) and '\x00' in value:
        stats['values with NUL characters removed'] += 1
        return value.replace('\x00', '')
    return value


def read_sqlite(path):
    """{table: (columns, rows)} for the school ID tables found in the SQLite file, read-only."""
    path = Path(path)
    if not path.is_file():
        raise MigrationError(f'{path} does not exist.')
    try:
        source = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
    except sqlite3.Error as error:
        raise MigrationError(f'Cannot open {path}: {error}') from None
    try:
        names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        tables = {}
        for table in TABLES:
            if table in names:
                columns = [row[1] for row in source.execute(f'PRAGMA table_info({table})')]
                tables[table] = (columns, source.execute(f'SELECT * FROM {table} ORDER BY id').fetchall())
    except sqlite3.Error as error:
        raise MigrationError(f'{path} is not a readable school ID database ({error}).') from None
    finally:
        source.close()
    if not tables:
        raise MigrationError(f'{path} has none of the school ID tables ({", ".join(TABLES)}).')
    return tables


def rename_statuses(columns, copied, notes):
    """Give an old inventory status its current name. Changes `copied` in place and says how many rows changed."""
    position, renamed = columns.index('status'), Counter()
    for number, values in enumerate(copied):
        if new := STATUS_RENAMES.get(values[position]):
            copied[number] = (*values[:position], new, *values[position + 1:])
            renamed[(values[position], new)] += 1
    for (old, new), count in renamed.items():
        notes.append(f'inventory: the status "{old}" is now called "{new}" ({count} rows changed).')


def table_columns(pg, table):
    """(column names, names PostgreSQL requires a value for) of a table in the current schema."""
    info = pg.execute(
        'SELECT column_name, is_nullable, column_default, is_identity FROM information_schema.columns '
        'WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position',
        (table,)).fetchall()
    want = [name for name, *_ in info]
    required = {name for name, nullable, default, identity in info
                if nullable == 'NO' and default is None and identity == 'NO'}
    return want, required


def copy_rows(have, rows, columns, invent_token, stats):
    """The SQLite rows cut down to `columns`, with a new QR token on each when the file has none."""
    positions = [have.index(name) for name in columns]
    copied = []
    for row in rows:
        values = [clean(row[position], stats) for position in positions]
        if invent_token:
            values.append(secrets.token_urlsafe(18))
        copied.append(tuple(values))
    return copied


def prepare_table(pg, table, have, rows, stats, notes):
    """(columns, rows) of one table ready to insert: only columns PostgreSQL has, NUL characters removed."""
    want, required = table_columns(pg, table)
    columns = [name for name in want if name in have]
    lacking = sorted(required - set(columns))
    invent_token = table == 'inventory' and lacking == ['token']      # the very first versions had none
    if lacking and not invent_token:
        raise MigrationError(f'The SQLite table {table} has no {", ".join(lacking)} column, which '
                             f'PostgreSQL needs. The file is older than this script supports.')
    copied = copy_rows(have, rows, columns, invent_token, stats)
    if invent_token:
        columns.append('token')
        notes.append('inventory: the SQLite file has no QR tokens, so new ones were made.')
    if table == 'inventory' and 'status' in columns:        # a status that has since been renamed
        rename_statuses(columns, copied, notes)
    if ignored := [name for name in have if name not in want]:
        notes.append(f'{table}: ignored columns that PostgreSQL does not have: {", ".join(ignored)}.')
    return columns, copied


def prepare(pg, tables, stats, notes):
    """{table: (columns, rows)} ready to insert."""
    return {table: prepare_table(pg, table, have, rows, stats, notes) for table, (have, rows) in tables.items()}


def check_student_ids(plan):
    """student_id is unique ignoring letter case in PostgreSQL. Say which ones clash, before copying."""
    columns, rows = plan.get('inventory', ([], []))
    if 'student_id' not in columns:
        return
    position, groups = columns.index('student_id'), {}
    for row in rows:
        groups.setdefault(row[position].lower(), []).append(row[position])
    if clashes := sorted(' = '.join(names) for names in groups.values() if len(names) > 1):
        raise MigrationError(
            'These student IDs are the same once letter case is ignored: ' + '; '.join(clashes)
            + '. Fix or delete the extra records, then run this again. (Nothing in the SQLite file was changed.)')


def make_room(pg, say):
    """The target must hold none of your data. The demo students of a first start are replaced."""
    counts = {table: pg.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES}
    if not any(counts.values()):
        return
    if only_untouched_demo_data(pg):
        say('The PostgreSQL database only holds the six demo students from a first start; replacing them.')
        pg.execute('DELETE FROM inventory')
        return
    filled = ', '.join(f'{table} ({count})' for table, count in counts.items() if count)
    raise MigrationError(
        f'The PostgreSQL database already has data: {filled}. Nothing is overwritten. Migrate into an empty '
        'database (for the Docker one: docker compose down -v, then docker compose up -d).')


def migrate(sqlite_path, database_url, dry_run=False, say=print):
    """Copy the SQLite file into PostgreSQL. Returns {table: rows copied}. Raises MigrationError."""
    tables = read_sqlite(sqlite_path)
    stats, notes = Counter(), []
    try:
        with psycopg.connect(database_url, connect_timeout=10) as pg:
            init_schema(pg)
            plan = prepare(pg, tables, stats, notes)
            check_student_ids(plan)
            make_room(pg, say)
            with pg.cursor() as cursor:
                for table, (columns, rows) in plan.items():
                    if rows:
                        cursor.executemany(
                            f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({", ".join(["%s"] * len(columns))})',
                            rows)
            for table, (columns, rows) in plan.items():
                stored = [tuple(row) for row in pg.execute(f'SELECT {", ".join(columns)} FROM {table} ORDER BY id')]
                if stored != rows:
                    raise MigrationError(f'{table}: the rows read back from PostgreSQL differ from the SQLite '
                                         'rows, so nothing was written.')
            for table in TABLES:        # new rows must continue after the highest copied id
                pg.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), "
                           f'MAX(id) IS NOT NULL) FROM {table}')
            if dry_run:
                pg.rollback()
    except psycopg.OperationalError as error:
        raise MigrationError(f'Cannot reach PostgreSQL: {str(error).strip()}') from None
    except psycopg.Error as error:
        raise MigrationError(f'PostgreSQL rejected the copy: {str(error).strip()}') from None
    for note in notes:
        say(f'Note: {note}')
    for what, count in stats.items():
        say(f'Note: {count} {what} (PostgreSQL cannot store them).')
    return {table: len(rows) for table, (_, rows) in plan.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('sqlite_file', nargs='?', type=Path, default=DEFAULT_SOURCE,
                        help='SQLite file to copy (default: %(default)s)')
    parser.add_argument('--database-url', default=os.environ.get('DATABASE_URL', '').strip(),
                        help='PostgreSQL URL (default: the DATABASE_URL variable)')
    parser.add_argument('--dry-run', action='store_true', help='check everything but write nothing')
    args = parser.parse_args(argv)
    if not args.database_url:
        print('Set DATABASE_URL (or pass --database-url) to the PostgreSQL database to copy into.', file=sys.stderr)
        return 2
    try:
        counts = migrate(args.sqlite_file, args.database_url, args.dry_run)
    except MigrationError as error:
        print(f'Nothing was copied. {error}', file=sys.stderr)
        return 1
    print(f'{"Checked (dry run, nothing written)" if args.dry_run else "Copied"} from {args.sqlite_file}:')
    for table in TABLES:
        print(f'  {table:<17} {counts.get(table, 0):>6} rows')
    if not args.dry_run:
        print('Every row was read back and matched. Keep the SQLite file as a backup until you have '
              'checked the records in the app.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
