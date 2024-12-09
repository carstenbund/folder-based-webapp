import logging
import sqlite3
from jinja2 import Environment, FileSystemLoader
import markdown
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server


class NLPApp:
    def __init__(self, template_dir='templates', db_path='users.db'):
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.db_path = db_path

    def convert_markdown(self, content):
        """Convert Markdown content to HTML."""
        if content:
            return markdown.markdown(content)
        return ""

    def render_template(self, template_name, context={}):
        logging.debug(f"Rendering template: {template_name} with context: {context}")
        template = self.env.get_template(template_name)
        return template.render(context)

    def get_main_entries(self):
            """Fetch all top-level entries (folders and files) from the database."""
            try:
                logging.debug("Fetching main entries from database.")
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT id, filename, entry_type, content FROM file_entries WHERE parent_id IS NULL")
                main_entries = cursor.fetchall()
                conn.close()
                logging.debug(f"Main entries fetched: {main_entries}")
                return main_entries
            except Exception as e:
                logging.error(f"Error fetching main entries: {e}")
                return []

    def get_main_entry_details(self, folder_id):
        """Fetch details of a folder entry and its associated file entries."""
        try:
            logging.debug(f"Fetching details for folder entry {folder_id}.")
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Fetch folder details
            cursor.execute('SELECT id, filename, content FROM file_entries WHERE id = ? AND entry_type = "folder"', (folder_id,))
            folder_entry = cursor.fetchone()
            
            if not folder_entry:
                logging.warning(f"No folder entry found for id {folder_id}.")
                return None, {"folders": [], "images": [], "audio": [], "videos": [], "text": [], "other": []}
            
            # Fetch associated entries
            cursor.execute('''
                SELECT id, filename, entry_type, file_type, content, position_marker 
                FROM file_entries 
                WHERE parent_id = ? 
                ORDER BY position_marker
            ''', (folder_id,))
            entries = cursor.fetchall()
            conn.close()
            
            # Organize entries into categories
            parsed_entries = {
                "folders": [],  # Subfolders
                "images": [],
                "audio": [],
                "videos": [],
                "text": [],
                "other": []
            }
            
            for id_, filename, entry_type, file_type, content, position_marker in entries:
                if content:
                    content = self.convert_markdown(content) if content else None
                    logging.debug(f"{content[:15]}")
                entry = {
                    "id": id_,
                    "filename": filename,
                    "entry_type": entry_type,
                    "file_type": file_type,
                    "content": content,
                    "position_marker": position_marker
                }
                if entry_type == "folder":
                    parsed_entries["folders"].append(entry)  # Separate folders
                elif file_type and file_type.startswith("image/"):
                    parsed_entries["images"].append(entry)
                elif file_type and file_type.startswith("audio/"):
                    parsed_entries["audio"].append(entry)
                elif file_type and file_type.startswith("video/"):
                    parsed_entries["videos"].append(entry)  # New video category
                elif content:
                    parsed_entries["text"].append(entry)
                else:
                    parsed_entries["other"].append(entry)
                    
            logging.debug(f"Folder entry details: {folder_entry}, parsed entries: {parsed_entries}")
            return folder_entry, parsed_entries
        except Exception as e:
            logging.error(f"Error fetching folder entry details: {e}")
            return None, {"folders": [], "images": [], "audio": [], "videos": [], "text": [], "other": []}
        
    def get_entry_by_id(self, entry_id):
        """Fetch a single entry by ID."""
        try:
            logging.debug(f"Fetching entry with ID {entry_id}")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, parent_id, filename, content FROM file_entries WHERE id = ?", (entry_id,))
                entry = cursor.fetchone()
                
            if entry:
                logging.debug(f"Fetched entry: {entry}")
                return {
                    "id": entry[0],
                    "parent_id": entry[1],
                    "filename": entry[2],
                    "content": entry[3]
                }
            else:
                logging.warning(f"No entry found with ID {entry_id}")
                return None
        except Exception as e:
            logging.error(f"Error fetching entry: {e}")
            return None
            
    def update_entry(self, entry_id, content):
        """Update an entry in the database."""
        try:
            logging.debug(f"Updating entry {entry_id} with new content")
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE file_entries SET content = ? WHERE id = ?", (content, entry_id))
            conn.commit()
            conn.close()
            logging.debug(f"Entry {entry_id} updated successfully")
            return True
        except Exception as e:
            logging.error(f"Error updating entry: {e}")
            return False        
        
    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '/')
        logging.debug(f"Handling request path: {path}")

        try:
            if path == '/':  # Index route
                main_entries = self.get_main_entries()
                html = self.render_template('index.html', {'main_entries': main_entries})
                start_response('200 OK', [('Content-Type', 'text/html')])
                logging.debug(f"main: 2")
                return [html.encode('utf-8')]

            elif path.startswith('/entry/'):  # Main entry details route
                main_entry_id = path.split('/')[2]
                main_entry, parsed_entries = self.get_main_entry_details(main_entry_id)
                if main_entry:
                    html = self.render_template('entry.html', {'main_entry': main_entry, 'parsed_entries': parsed_entries})
                    start_response('200 OK', [('Content-Type', 'text/html')])
                    return [html.encode('utf-8')]
                else:
                    start_response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b"Main entry not found"]
        
            elif path.startswith('/edit/'):  # Edit route
                entry_id = path.split('/')[2]
                entry = self.get_entry_by_id(entry_id)
                if entry:
                    html = self.render_template('edit.html', {'entry': entry})
                    start_response('200 OK', [('Content-Type', 'text/html')])
                    return [html.encode('utf-8')]
                else:
                    start_response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b"Entry not found"]

        
            elif path == '/save':  # Save route
                if environ['REQUEST_METHOD'] == 'POST':
                    try:
                        # Parse POST data
                        content_length = int(environ.get('CONTENT_LENGTH', 0))
                        post_data = environ['wsgi.input'].read(content_length).decode('utf-8')
                        params = parse_qs(post_data)
                        entry_id = params.get('id', [None])[0]
                        content = params.get('content', [None])[0]
                        parent_id = params.get('parent_id', [None])[0]
                        
                        if entry_id and content is not None:
                            # Use the existing update_entry function
                            success = self.update_entry(entry_id, content)
                            if success:
                                # Redirect based on parent_id
                                if parent_id:
                                    start_response('302 Found', [('Location', f'/entry/{parent_id}')])
                                else:
                                    start_response('302 Found', [('Location', f'/entry/{entry_id}')])  # Default to the current entry
                                return [b"Redirecting..."]
                
                        start_response('400 Bad Request', [('Content-Type', 'text/plain')])
                        return [b"Invalid data provided"]
                    except Exception as e:
                        logging.error(f"Error in save route: {e}")
                        start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
                        return [b"Internal Server Error"]
                    
            else:  # 404 for unknown paths
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b"Page not found"]

        except Exception as e:
            logging.error(f"Unhandled exception: {e}")
            start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
            return [b"Internal Server Error"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Configure application
    app = NLPApp(template_dir='templates', db_path='data_import.sqlite')

    # Run local web server
    port = 8080
    logging.info(f"Starting local server at http://localhost:{port}")
    with make_server('', port, app) as server:
        logging.info(f"Serving on port {port}...")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Server stopped.")

            