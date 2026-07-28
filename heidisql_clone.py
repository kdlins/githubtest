import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sqlite3
import csv
import re
import threading
import queue
from datetime import datetime
import pandas as pd
import io

try:
    import ttkbootstrap as ttkb
    from ttkbootstrap.constants import *
    USE_BOOTSTRAP = True
except ImportError:
    USE_BOOTSTRAP = False
    print("Warning: ttkbootstrap not found. Using standard tkinter theme.")

class DatabaseManager:
    def __init__(self, db_path="local_data.sqlite"):
        self.db_path = db_path
        self.conn = None
        self.connect()

    def connect(self):
        if self.conn:
            self.conn.close()
        self.conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = sqlite3.Row

    def execute_query(self, sql):
        """Executes a query. Supports multiple statements separated by ;"""
        cursor = self.conn.cursor()
        try:
            # Simple MySQL to SQLite adaptation (mostly compatible, but handle backticks)
            sql = sql.replace('`', '"') 
            
            # Split into multiple statements if needed (basic split)
            statements = [s.strip() for s in sql.split(';') if s.strip()]
            
            results = []
            last_result = None
            
            for stmt in statements:
                if stmt.upper().startswith("SELECT"):
                    cursor.execute(stmt)
                    cols = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    last_result = {"columns": cols, "data": rows}
                    results.append(last_result)
                else:
                    cursor.execute(stmt)
                    self.conn.commit()
                    last_result = {"rows_affected": cursor.rowcount}
                    results.append(last_result)
            
            return {"success": True, "results": results, "message": "Query executed successfully."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        return [row[0] for row in cursor.fetchall()]

    def get_table_info(self, table_name):
        cursor = self.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name});")
        return cursor.fetchall()

    def infer_type(self, value):
        """Infers SQLite type based on value string."""
        if value is None or value == '':
            return 'VARCHAR'
        
        val_str = str(value).strip()
        
        # Rule: Starts with '0' -> VARCHAR (unless it's just 0 or 0.0)
        if val_str.startswith('0') and len(val_str) > 1:
            if '.' not in val_str:
                return 'VARCHAR'
        
        # Rule: Contains decimal point -> DECIMAL(10,4)
        if '.' in val_str:
            try:
                float(val_str)
                return 'DECIMAL'
            except ValueError:
                return 'VARCHAR'
        
        # Default to VARCHAR for safety as per requirements mostly focusing on these two
        return 'VARCHAR'

    def create_table_from_csv(self, table_name, file_path):
        try:
            df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
            
            if df.empty:
                return {"success": False, "error": "CSV file is empty"}

            # Infer schema
            columns_def = []
            for col in df.columns:
                # Clean column names for SQLite
                safe_col = col.replace('"', '""') 
                sample_val = df[col].dropna().iloc[0] if not df[col].dropna().empty else ''
                col_type = self.infer_type(sample_val)
                
                if col_type == 'DECIMAL':
                    columns_def.append(f'"{safe_col}" DECIMAL(10,4)')
                else:
                    columns_def.append(f'"{safe_col}" VARCHAR')

            # Add an implicit primary key if none detected? 
            # Requirement: "check for duplicates based on the primary key".
            # Since CSV doesn't define PK, we assume the first column or a combination?
            # Heidisql usually asks. Here, let's assume the user wants to append based on ALL columns 
            # OR we create an auto-increment ID. 
            # Let's stick to the prompt: "check for duplicates based on the primary key".
            # We will treat the FIRST column as the logical PK for duplicate checking during import.
            pk_col = df.columns[0].replace('"', '""')
            
            create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ("_id" INTEGER PRIMARY KEY AUTOINCREMENT, {", ".join(columns_def)})'
            
            # We need to track which original column is PK for upsert logic later. 
            # For simplicity in this clone, we will store metadata or just use the first data column for matching.
            
            cursor = self.conn.cursor()
            cursor.execute(create_sql)
            
            # Insert Data
            # We need to map DF columns to DB columns (skipping _id)
            db_cols = [c.split('"')[1] for c in columns_def] # Extract clean names
            
            placeholders = ", ".join(["?" for _ in db_cols])
            insert_sql = f'INSERT INTO "{table_name}" ({", ".join([f\'"{c}"\' for c in db_cols])}) VALUES ({placeholders})'
            
            data_tuples = [tuple(row) for row in df[df.columns].values]
            
            cursor.executemany(insert_sql, data_tuples)
            self.conn.commit()
            
            return {"success": True, "message": f"Table '{table_name}' created/imported successfully."}
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    def import_csv_to_existing(self, table_name, file_path):
        try:
            df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
            if df.empty:
                return {"success": False, "error": "CSV file is empty"}

            # Get existing schema
            info = self.get_table_info(table_name)
            # info format: (cid, name, type, notnull, dflt_value, pk)
            # Filter out our internal _id
            real_cols = [row[1] for row in info if row[1] != '_id']
            
            if not real_cols:
                return {"success": False, "error": "Target table has no columns"}

            # Assume first column is PK for duplicate check as per heuristic
            pk_col = real_cols[0]
            
            # Prepare data
            # Ensure DF has same columns
            missing = set(real_cols) - set(df.columns)
            if missing:
                return {"success": False, "error": f"CSV missing columns: {missing}"}
            
            df = df[real_cols] # Reorder to match DB
            
            cursor = self.conn.cursor()
            count_inserted = 0
            
            # Transaction for speed
            cursor.execute("BEGIN")
            
            for _, row in df.iterrows():
                # Check duplicate based on PK
                pk_val = row[pk_col]
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE "{pk_col}" = ?', (pk_val,))
                if cursor.fetchone()[0] == 0:
                    # No duplicate, insert
                    vals = [row[c] for c in real_cols]
                    placeholders = ", ".join(["?" for _ in real_cols])
                    cols_str = ", ".join([f'"{c}"' for c in real_cols])
                    cursor.execute(f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({placeholders})', vals)
                    count_inserted += 1
            
            self.conn.commit()
            return {"success": True, "message": f"Imported {count_inserted} new rows. Duplicates skipped."}
            
        except Exception as e:
            return {"success": False, "error": str(e)}

class QueryTab(ttk.Frame):
    def __init__(self, parent, db_manager, on_export_request):
        super().__init__(parent)
        self.db = db_manager
        self.on_export_request = on_export_request
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=2)
        
        self.btn_run = ttk.Button(toolbar, text="▶ Run (F9)", command=self.run_query)
        self.btn_run.pack(side="left", padx=5)
        
        self.btn_import_new = ttk.Button(toolbar, text="📂 Create Table from CSV", command=lambda: self.import_csv(mode='new'))
        self.btn_import_new.pack(side="left", padx=5)

        self.btn_import_append = ttk.Button(toolbar, text="📥 Append CSV to Table", command=lambda: self.import_csv(mode='append'))
        self.btn_import_append.pack(side="left", padx=5)
        
        lbl_status = ttk.Label(toolbar, text="Status: Ready", foreground="gray")
        lbl_status.pack(side="right", padx=5)
        self.lbl_status = lbl_status

        # SQL Editor
        self.txt_sql = tk.Text(self, height=10, font=("Consolas", 11), wrap="none")
        self.txt_sql.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        
        # Scrollbar for SQL
        sql_scroll = ttk.Scrollbar(self.txt_sql, orient="vertical", command=self.txt_sql.yview)
        sql_scroll.pack(side="right", fill="y")
        self.txt_sql.config(yscrollcommand=sql_scroll.set)

        # Result Area
        res_frame = ttk.LabelFrame(self, text="Data Output")
        res_frame.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
        res_frame.grid_rowconfigure(0, weight=1)
        res_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(res_frame, selectmode="extended")
        self.tree.grid(row=0, column=0, sticky="nsew")
        
        tree_scroll_y = ttk.Scrollbar(res_frame, orient="vertical", command=self.tree.yview)
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll_y.set)
        
        tree_scroll_x = ttk.Scrollbar(res_frame, orient="horizontal", command=self.tree.xview)
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=tree_scroll_x.set)

        # Context Menu for Export
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Copy Selected (Excel CSV)", command=lambda: self.export_to_clipboard('csv'))
        self.menu.add_command(label="Copy Selected (SQL Inserts)", command=lambda: self.export_to_clipboard('sql'))
        
        self.tree.bind("<Button-3>", self.show_context_menu)
        
        # Bind F9
        self.txt_sql.bind("<F9>", lambda e: self.run_query())

    def show_context_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def run_query(self):
        sql = self.txt_sql.get("1.0", tk.END).strip()
        if not sql:
            return
        
        self.lbl_status.config(text="Running...")
        self.update()
        
        result = self.db.execute_query(sql)
        
        # Clear tree
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = []

        if result["success"]:
            last_res = result["results"][-1]
            if "columns" in last_res:
                cols = last_res["columns"]
                self.tree["columns"] = cols
                self.tree.heading("#0", text="#")
                for c in cols:
                    self.tree.heading(c, text=c)
                    self.tree.column(c, width=100, anchor="w")
                
                for i, row in enumerate(last_res["data"]):
                    values = list(row)
                    self.tree.insert("", "end", iid=i, text=str(i+1), values=values)
                
                self.lbl_status.config(text=f"Success. {len(last_res['data'])} rows.")
            else:
                self.lbl_status.config(text=f"Success. Rows affected: {last_res.get('rows_affected', 0)}")
        else:
            messagebox.showerror("SQL Error", result["error"])
            self.lbl_status.config(text="Error")

    def import_csv(self, mode='new'):
        table_name = None
        if mode == 'append':
            tables = self.db.get_tables()
            if not tables:
                messagebox.showwarning("Warning", "No tables exist to append to.")
                return
            
            # Simple dialog for table selection
            top = tk.Toplevel(self)
            top.title("Select Table")
            ttk.Label(top, text="Select Target Table:").pack(pady=10)
            combo = ttk.Combobox(top, values=tables, state="readonly")
            combo.pack(pady=5)
            combo.current(0)
            
            def confirm():
                nonlocal table_name
                table_name = combo.get()
                top.destroy()
                proceed()
            
            ttk.Button(top, text="OK", command=confirm).pack(pady=10)
            top.wait_window()
            
            if not table_name:
                return
        else:
            table_name = simple_dialog("New Table Name", "Enter name for new table:")
            if not table_name:
                return

        file_path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if not file_path:
            return

        if mode == 'new':
            res = self.db.create_table_from_csv(table_name, file_path)
        else:
            res = self.db.import_csv_to_existing(table_name, file_path)

        if res["success"]:
            messagebox.showinfo("Import", res["message"])
            # Refresh tree in main app could be triggered here via callback if needed
        else:
            messagebox.showerror("Import Failed", res["error"])

    def export_to_clipboard(self, fmt):
        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showwarning("Export", "No rows selected.")
            return
        
        # Get column names
        cols = self.tree["columns"]
        
        data_rows = []
        for iid in selected_ids:
            values = self.tree.item(iid, "values")
            data_rows.append(values)
        
        output = ""
        if fmt == 'csv':
            # Excel compatible CSV (tab separated often works best for paste into Excel, or comma with quotes)
            # Using Tab-Separated Values for direct Excel paste compatibility
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(cols)
            for row in data_rows:
                writer.writerow(row)
            output = buffer.getvalue()
            
        elif fmt == 'sql':
            table_guess = "unknown_table" # In a real app, we'd know the source table
            lines = []
            for row in data_rows:
                vals = []
                for v in row:
                    if v is None:
                        vals.append("NULL")
                    else:
                        # Escape single quotes
                        sv = str(v).replace("'", "''")
                        vals.append(f"'{sv}'")
                line = f"INSERT INTO {table_guess} VALUES ({', '.join(vals)});"
                lines.append(line)
            output = "\n".join(lines)

        self.clipboard_clear()
        self.clipboard_append(output)
        self.lbl_status.config(text=f"Copied {len(data_rows)} rows to clipboard ({fmt})")

def simple_dialog(title, prompt):
    top = tk.Toplevel()
    top.title(title)
    top.transient() # Modal-ish
    
    frame = ttk.Frame(top, padding=20)
    frame.pack()
    
    ttk.Label(frame, text=prompt).pack(anchor="w")
    entry = ttk.Entry(frame, width=40)
    entry.pack(pady=10, fill="x")
    entry.focus()
    
    result = {"value": None}
    
    def submit():
        result["value"] = entry.get().strip()
        top.destroy()
    
    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=10)
    ttk.Button(btn_frame, text="OK", command=submit).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Cancel", command=top.destroy).pack(side="left", padx=5)
    
    top.bind('<Return>', lambda e: submit())
    top.wait_window()
    
    return result["value"]

class HeidiSQLCloneApp:
    def __init__(self, root):
        self.root = root
        if USE_BOOTSTRAP:
            self.style = ttkb.Style(theme="litera") # Clean, professional look
            self.root.title("HeidiSQL Clone (Python)")
        else:
            self.root.title("HeidiSQL Clone (Standard)")
        
        self.root.geometry("1200x800")
        
        self.db = DatabaseManager()
        
        # Layout: Sidebar + Main
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Sidebar
        self.sidebar = ttk.Frame(root, width=250, relief="sunken", borderwidth=1)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.rowconfigure(1, weight=1)
        
        ttk.Label(self.sidebar, text="Databases", font=("Arial", 12, "bold")).pack(pady=10)
        
        self.tree_db = ttk.Treeview(self.sidebar, show="tree")
        self.tree_db.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(self.sidebar)
        btn_frame.pack(fill="x", padx=5, pady=5)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_db_tree).pack(side="left", expand=True, fill="x")
        
        # Main Area (Notebook)
        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=0, column=1, sticky="nsew", padx=2, pady=2)
        
        # Initial Tab
        self.add_query_tab()
        
        self.refresh_db_tree()

    def add_query_tab(self):
        tab = QueryTab(self.notebook, self.db, None)
        self.notebook.add(tab, text=f"Query {self.notebook.index('end')+1}")
        self.notebook.select(tab)

    def refresh_db_tree(self):
        self.tree_db.delete(*self.tree_db.get_children())
        db_node = self.tree_db.insert("", "end", text="local_data.sqlite", open=True)
        
        tables = self.db.get_tables()
        for t in tables:
            self.tree_db.insert(db_node, "end", text=t, tags=("table",))
        
        self.tree_db.tag_bind("table", "<Double-1>", lambda e: self.on_table_double_click())

    def on_table_double_click(self):
        sel = self.tree_db.selection()
        if not sel:
            return
        item = self.tree_db.item(sel[0])
        table_name = item["text"]
        
        # Open new tab with SELECT *
        tab = QueryTab(self.notebook, self.db, None)
        tab.txt_sql.insert("1.0", f"SELECT * FROM `{table_name}` LIMIT 100;")
        self.notebook.add(tab, text=table_name)
        self.notebook.select(tab)
        tab.run_query()

if __name__ == "__main__":
    root = tk.Tk()
    # Try to set a nice default font
    try:
        root.option_add("*Font", "Segoe UI 10")
    except:
        pass
    
    app = HeidiSQLCloneApp(root)
    root.mainloop()
