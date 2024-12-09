import os
import sqlite3
import mimetypes
import re
from striprtf.striprtf import rtf_to_text
import chardet
import logging

class RTFImporter:
    def __init__(self, base_path, db_path):
        self.base_path = base_path
        self.db_path = db_path

        # Configure logging
        logging.basicConfig(filename="debug_log.txt", level=logging.DEBUG,
                            format="%(asctime)s - %(levelname)s - %(message)s")

    def extract_position_marker(self, filename):
        """Extracts the position marker from the filename."""
        match = re.match(r"^(\d+)[-_]", filename)
        return int(match.group(1)) if match else None

    def process_file(self, file_path):
        """Processes a single RTF file."""
        logging.info(f"Processing file: {file_path}")
        try:
            with open(file_path, "rb") as f:
                raw_data = f.read()
                detected = chardet.detect(raw_data)
                encoding = detected.get('encoding', 'utf-8')  # Default to UTF-8 if detection fails
                logging.debug(f"Detected encoding for {file_path}: {encoding}")

            with open(file_path, "r", encoding=encoding, errors='ignore') as f:
                raw_text = f.read()
                content = rtf_to_text(raw_text)
                logging.info(f"Processed content from {file_path} successfully.")
                return content

        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")
            return None

    def setup_database(self):
        """Sets up the SQLite database and tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create unified table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_entries (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER, -- For nesting folders
            filename TEXT NOT NULL, -- Folder name for folders
            entry_type TEXT CHECK(entry_type IN ('folder', 'file')),
            file_type TEXT, -- NULL for folders
            content TEXT, -- NULL for folders
            position_marker INTEGER, -- NULL for folders
            FOREIGN KEY (parent_id) REFERENCES file_entries (id)
        )
        """)
        conn.commit()
        conn.close()
        
        
    def import_to_sqlite(self):
        """Imports data into SQLite database with the updated schema."""
        self.setup_database()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Traverse directory and build hierarchy
        folder_stack = [(None, self.base_path)]  # (parent_id, folder_path)
        
        while folder_stack:
            parent_id, folder_path = folder_stack.pop()
            folder_name = os.path.basename(folder_path)
            title = None
            
            # Process title.rtf if exists
            title_file = os.path.join(folder_path, "titel.rtf")
            logging.debug(f"testing for title: {title_file}")
            if os.path.isfile(title_file):
                title = self.process_file(title_file)
                logging.debug(f"Processing folder title:{title_file}, {title}")
                
            logging.debug(f"Processing folder: {folder_path}")
            # Insert folder entry
            cursor.execute("INSERT INTO file_entries (parent_id, filename, entry_type, content) VALUES (?, ?, ?, ?)",
                            (parent_id, folder_name, 'folder', title))
            folder_id = cursor.lastrowid
            
            try:
                # Process files and subdirectories
                for item in os.listdir(folder_path):
                    item_path = os.path.join(folder_path, item)
                    if os.path.isdir(item_path):
                        folder_stack.append((folder_id, item_path))  # Queue subfolder
                    elif os.path.isfile(item_path):
                        if item.endswith("titel.rtf") or item.startswith("."):
                            continue  # Skip title.rtf and hidden files
                        
                        file_type, _ = mimetypes.guess_type(item_path)
                        position_marker = self.extract_position_marker(item)
                        content = None
                        
                        if item.endswith(".rtf"):
                            content = self.process_file(item_path)
                            
                        # Insert file entry
                        cursor.execute("""
                        INSERT INTO file_entries (parent_id, filename, entry_type, file_type, content, position_marker)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """, (folder_id, item, 'file', file_type, content, position_marker))
                conn.commit()
            except Exception as e:
                logging.error(f"Error processing folder {folder_path}: {e}")
                continue  # Skip problematic folder but continue processing others
            
        conn.close()
        print("Import complete.")

            
# Example usage
if __name__ == "__main__":
    base_path = "./"  # Replace with the actual path to your files
    db_path = "data_import.sqlite"

    importer = RTFImporter(base_path, db_path)
    importer.import_to_sqlite()
    
