#!/usr/bin/env python3
"""
HeidiSQL-like Application
A Python program for managing SQLite databases with CSV import capabilities.
Supports MySQL-style queries, table creation from CSV, and data management.
"""

import sqlite3
import csv
import re
import os
from typing import List, Tuple, Optional, Any


class HeidiSQLApp:
    """Main application class for HeidiSQL-like functionality."""
    
    def __init__(self, db_path: str = "heidi.db"):
        """Initialize the application with a SQLite database."""
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.connect()
    
    def connect(self) -> None:
        """Establish connection to the SQLite database."""
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        print(f"Connected to database: {self.db_path}")
    
    def disconnect(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            print("Database connection closed.")
    
    def _detect_field_type(self, value: str) -> str:
        """
        Detect field type based on value content.
        - Values starting with '0' are treated as VARCHAR
        - Values containing a decimal point are DECIMAL(10,4)
        - Otherwise VARCHAR
        """
        if not value:
            return "VARCHAR"
        
        # Strip whitespace
        value = value.strip()
        
        # Empty after strip
        if not value:
            return "VARCHAR"
        
        # Values starting with '0' are VARCHAR
        if value.startswith('0'):
            return "VARCHAR"
        
        # Check if it contains a decimal point
        if '.' in value:
            # Verify it's a valid decimal number
            try:
                float(value)
                return "DECIMAL(10,4)"
            except ValueError:
                return "VARCHAR"
        
        return "VARCHAR"
    
    def _sanitize_identifier(self, name: str) -> str:
        """Sanitize identifier names for SQL safety."""
        # Remove special characters, keep alphanumeric and underscore
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = '_' + sanitized
        return sanitized
    
    def create_table_from_csv(self, csv_path: str, table_name: str) -> bool:
        """
        Create a table from a CSV file.
        Field types are derived from the header row and first data row.
        """
        if not os.path.exists(csv_path):
            print(f"Error: CSV file '{csv_path}' not found.")
            return False
        
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                
                # Get header row
                headers = next(reader)
                
                # Sanitize column names
                columns = [self._sanitize_identifier(h) for h in headers]
                
                # Get first data row to detect types
                first_row = next(reader, None)
                
                if first_row:
                    # Detect types from first row
                    col_types = []
                    for value in first_row:
                        col_type = self._detect_field_type(value)
                        col_types.append(col_type)
                else:
                    # Default to VARCHAR if no data
                    col_types = ["VARCHAR"] * len(columns)
                
                # Build CREATE TABLE statement
                col_defs = []
                for col, col_type in zip(columns, col_types):
                    col_defs.append(f'"{col}" {col_type}')
                
                # Add primary key (auto-increment integer)
                create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                create_sql += ', '.join(col_defs)
                create_sql += ')'
                
                # Execute CREATE TABLE
                self.cursor.execute(create_sql)
                self.conn.commit()
                
                # Insert first row if it exists
                if first_row:
                    placeholders = ', '.join(['?' for _ in first_row])
                    col_names = ', '.join(['"' + c + '"' for c in columns])
                    insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                    self.cursor.execute(insert_sql, first_row)
                
                # Insert remaining rows
                for row in reader:
                    if len(row) == len(columns):
                        placeholders = ', '.join(['?' for _ in row])
                        col_names = ', '.join(['"' + c + '"' for c in columns])
                        insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                        self.cursor.execute(insert_sql, row)
                
                self.conn.commit()
                print(f"Table '{table_name}' created successfully with {len(columns)} columns.")
                return True
                
        except Exception as e:
            print(f"Error creating table from CSV: {e}")
            return False
    
    def import_csv_to_table(self, csv_path: str, table_name: str, pk_column: str = None) -> bool:
        """
        Import CSV data into an existing table.
        Checks for duplicates based on primary key if specified.
        """
        if not os.path.exists(csv_path):
            print(f"Error: CSV file '{csv_path}' not found.")
            return False
        
        try:
            # Check if table exists
            self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            if not self.cursor.fetchone():
                print(f"Error: Table '{table_name}' does not exist.")
                return False
            
            # Get table columns
            self.cursor.execute(f'PRAGMA table_info("{table_name}")')
            table_info = self.cursor.fetchall()
            columns = [col[1] for col in table_info]  # Column names
            
            # Find primary key column if not specified
            if pk_column is None:
                for col in table_info:
                    if col[5] == 1:  # pk flag
                        pk_column = col[1]
                        break
            
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                
                # Get header row
                headers = next(reader)
                headers = [self._sanitize_identifier(h) for h in headers]
                
                # Map CSV columns to table columns
                col_mapping = {}
                for i, h in enumerate(headers):
                    if h in columns:
                        col_mapping[h] = i
                
                if not col_mapping:
                    print("Error: No matching columns found between CSV and table.")
                    return False
                
                imported_count = 0
                skipped_count = 0
                
                for row in reader:
                    # Extract values for matching columns
                    values = [row[col_mapping[col]] if col in col_mapping else None 
                              for col in columns if col != 'id']
                    
                    # Check for duplicate if pk_column is specified and in mapping
                    if pk_column and pk_column in col_mapping:
                        pk_value = row[col_mapping[pk_column]]
                        
                        # Check if duplicate exists
                        self.cursor.execute(
                            f'SELECT COUNT(*) FROM "{table_name}" WHERE "{pk_column}" = ?',
                            (pk_value,)
                        )
                        count = self.cursor.fetchone()[0]
                        
                        if count > 0:
                            skipped_count += 1
                            continue
                    
                    # Insert the row
                    target_columns = [col for col in columns if col != 'id']
                    placeholders = ', '.join(['?' for _ in target_columns])
                    col_names = ', '.join(['"' + c + '"' for c in target_columns])
                    insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                    
                    try:
                        self.cursor.execute(insert_sql, values)
                        imported_count += 1
                    except sqlite3.IntegrityError:
                        skipped_count += 1
                
                self.conn.commit()
                print(f"Import completed: {imported_count} rows imported, {skipped_count} rows skipped (duplicates).")
                return True
                
        except Exception as e:
            print(f"Error importing CSV to table: {e}")
            return False
    
    def execute_query(self, query: str) -> Optional[Any]:
        """
        Execute a MySQL-style SQL query.
        Returns results for SELECT queries, None for other queries.
        """
        try:
            # Convert some MySQL syntax to SQLite if needed
            # Handle LIMIT (works in both)
            # Handle backticks for identifiers
            query = query.replace('`', '"')
            
            # Execute the query
            self.cursor.execute(query)
            
            # Check if it's a SELECT or PRAGMA query that returns data
            query_upper = query.strip().upper()
            if query_upper.startswith('SELECT') or query_upper.startswith('PRAGMA'):
                results = self.cursor.fetchall()
                # Get column names
                column_names = [description[0] for description in self.cursor.description]
                return {'columns': column_names, 'rows': results}
            else:
                self.conn.commit()
                affected = self.cursor.rowcount
                print(f"Query executed successfully. Affected rows: {affected}")
                return None
                
        except sqlite3.Error as e:
            print(f"SQL Error: {e}")
            return None
    
    def show_tables(self) -> List[str]:
        """List all tables in the database."""
        self.cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in self.cursor.fetchall()]
        return tables
    
    def describe_table(self, table_name: str) -> List[Tuple]:
        """Show table structure (similar to DESCRIBE in MySQL)."""
        try:
            self.cursor.execute(f'PRAGMA table_info("{table_name}")')
            return self.cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Error: {e}")
            return []
    
    def get_table_data(self, table_name: str, limit: int = 100) -> Optional[dict]:
        """Get data from a table with optional limit."""
        query = f'SELECT * FROM "{table_name}" LIMIT {limit}'
        return self.execute_query(query)
    
    def create_empty_table(self, table_name: str, columns: List[Tuple[str, str]]) -> bool:
        """
        Create an empty table with specified columns.
        columns: List of (column_name, column_type) tuples
        """
        try:
            col_defs = []
            for col_name, col_type in columns:
                col_name = self._sanitize_identifier(col_name)
                col_type = col_type.upper()
                if col_type not in ['VARCHAR', 'DECIMAL(10,4)', 'INTEGER', 'TEXT', 'REAL']:
                    col_type = 'VARCHAR'
                col_defs.append(f'"{col_name}" {col_type}')
            
            # Add primary key
            create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            create_sql += ', '.join(col_defs)
            create_sql += ')'
            
            self.cursor.execute(create_sql)
            self.conn.commit()
            print(f"Table '{table_name}' created successfully.")
            return True
            
        except Exception as e:
            print(f"Error creating table: {e}")
            return False


def print_results(results: dict) -> None:
    """Pretty print query results."""
    if not results or not results['rows']:
        print("(No results)")
        return
    
    columns = results['columns']
    rows = results['rows']
    
    # Calculate column widths
    widths = [len(str(col)) for col in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    
    # Print header
    header = ' | '.join(str(col).ljust(widths[i]) for i, col in enumerate(columns))
    print(header)
    print('-' * len(header))
    
    # Print rows
    for row in rows:
        print(' | '.join(str(val).ljust(widths[i]) for i, val in enumerate(row)))
    
    print(f"\n{len(rows)} row(s) returned.")


def interactive_mode():
    """Run the application in interactive mode."""
    app = HeidiSQLApp()
    
    print("\n" + "="*60)
    print("HeidiSQL-like Application")
    print("="*60)
    print("\nCommands:")
    print("  QUERY <sql>     - Execute a SQL query")
    print("  SHOW TABLES     - List all tables")
    print("  DESCRIBE <tbl>  - Show table structure")
    print("  SELECT <tbl>    - View table data (first 100 rows)")
    print("  IMPORT_CSV <file> <table> [pk] - Import CSV to existing table")
    print("  CREATE_CSV <file> <table>      - Create table from CSV")
    print("  CREATE <table> <col1:type>,<col2:type>... - Create empty table")
    print("  HELP            - Show this help")
    print("  EXIT            - Exit the application")
    print("="*60 + "\n")
    
    while True:
        try:
            cmd = input("heidi> ").strip()
            
            if not cmd:
                continue
            
            parts = cmd.split(None, 1)
            command = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            
            if command == "EXIT" or command == "QUIT":
                app.disconnect()
                print("Goodbye!")
                break
            
            elif command == "HELP":
                print("\nCommands:")
                print("  QUERY <sql>     - Execute a SQL query")
                print("  SHOW TABLES     - List all tables")
                print("  DESCRIBE <tbl>  - Show table structure")
                print("  SELECT <tbl>    - View table data (first 100 rows)")
                print("  IMPORT_CSV <file> <table> [pk] - Import CSV to existing table")
                print("  CREATE_CSV <file> <table>      - Create table from CSV")
                print("  CREATE <table> <col1:type>,<col2:type>... - Create empty table")
                print("  HELP            - Show this help")
                print("  EXIT            - Exit the application\n")
            
            elif command == "SHOW":
                if args.upper() == "TABLES":
                    tables = app.show_tables()
                    if tables:
                        print("\nTables in database:")
                        for t in tables:
                            print(f"  - {t}")
                    else:
                        print("No tables found.")
                else:
                    print("Usage: SHOW TABLES")
            
            elif command == "DESCRIBE":
                if args:
                    result = app.describe_table(args)
                    if result:
                        print(f"\nStructure of table '{args}':")
                        print("cid | name | type | notnull | dflt_value | pk")
                        print("-" * 50)
                        for row in result:
                            print(f"{row[0]:3} | {row[1]:15} | {row[2]:12} | {row[3]:7} | {str(row[4]):10} | {row[5]}")
                    else:
                        print(f"Table '{args}' not found.")
                else:
                    print("Usage: DESCRIBE <table_name>")
            
            elif command == "SELECT":
                if args:
                    result = app.get_table_data(args)
                    if result and result['rows']:
                        print_results(result)
                    else:
                        print(f"Table '{args}' is empty or not found.")
                else:
                    print("Usage: SELECT <table_name>")
            
            elif command == "QUERY":
                if args:
                    result = app.execute_query(args)
                    if result and isinstance(result, dict):
                        print_results(result)
                else:
                    print("Usage: QUERY <SQL statement>")
            
            elif command == "IMPORT_CSV":
                parts = args.split()
                if len(parts) >= 2:
                    csv_file = parts[0]
                    table = parts[1]
                    pk = parts[2] if len(parts) > 2 else None
                    app.import_csv_to_table(csv_file, table, pk)
                else:
                    print("Usage: IMPORT_CSV <csv_file> <table_name> [primary_key_column]")
            
            elif command == "CREATE_CSV":
                parts = args.split()
                if len(parts) >= 2:
                    csv_file = parts[0]
                    table = parts[1]
                    app.create_table_from_csv(csv_file, table)
                else:
                    print("Usage: CREATE_CSV <csv_file> <table_name>")
            
            elif command == "CREATE":
                parts = args.split(None, 1)
                if len(parts) >= 2:
                    table = parts[0]
                    cols_str = parts[1]
                    columns = []
                    for col_def in cols_str.split(','):
                        col_def = col_def.strip()
                        if ':' in col_def:
                            name, dtype = col_def.split(':', 1)
                            columns.append((name.strip(), dtype.strip()))
                        else:
                            columns.append((col_def, 'VARCHAR'))
                    app.create_empty_table(table, columns)
                else:
                    print("Usage: CREATE <table_name> <col1:type>,<col2:type>,...")
            
            else:
                print(f"Unknown command: {command}. Type HELP for available commands.")
                
        except KeyboardInterrupt:
            print("\nUse EXIT to quit.")
        except Exception as e:
            print(f"Error: {e}")


def main():
    """Main entry point."""
    import sys
    
    if len(sys.argv) > 1:
        # Command-line mode
        app = HeidiSQLApp()
        
        if sys.argv[1] == "--create-csv" and len(sys.argv) >= 4:
            app.create_table_from_csv(sys.argv[2], sys.argv[3])
        elif sys.argv[1] == "--import-csv" and len(sys.argv) >= 4:
            pk = sys.argv[4] if len(sys.argv) > 4 else None
            app.import_csv_to_table(sys.argv[2], sys.argv[3], pk)
        elif sys.argv[1] == "--query" and len(sys.argv) >= 3:
            result = app.execute_query(" ".join(sys.argv[2:]))
            if result and isinstance(result, dict):
                print_results(result)
        elif sys.argv[1] == "--show-tables":
            tables = app.show_tables()
            for t in tables:
                print(t)
        else:
            print("Usage:")
            print("  python heidisql.py                          - Interactive mode")
            print("  python heidisql.py --create-csv <file> <tbl> - Create table from CSV")
            print("  python heidisql.py --import-csv <file> <tbl> [pk] - Import CSV")
            print("  python heidisql.py --query \"<SQL>\"          - Execute query")
            print("  python heidisql.py --show-tables             - List tables")
        
        app.disconnect()
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
