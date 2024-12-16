#!/usr/bin/env python3
# app server with updated sql schema for navigation
import os, sys, io
import logging
import sqlite3
from jinja2 import Environment, FileSystemLoader
import markdown
from urllib.parse import urlparse, parse_qs, unquote
#from wsgiref.simple_server import make_server

EDIT_MODE = True

# Force UTF-8 encoding for stdout and stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class NLPApp:
    def __init__(self, static_dir ="static", template_dir='/var/www/natur-lehrpfad.de/app/templates', db_path='/var/www/natur-lehrpfad.de/app/lehr_pfad.db'):
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.db_path = db_path
        self.static_dir = static_dir

    logging.basicConfig(
        level=logging.DEBUG,  # Log level
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler('/var/log/apache2/nlpapp_debug.log'),  # Write to a custom log file
            logging.StreamHandler(sys.stderr)  # Still send logs to Apache's error log
        ]
    )


    def convert_markdown(self, content):
        """Convert Markdown content to HTML."""
        if content:
            return markdown.markdown(content)
        return ""

    def render_template(self, template_name, context={}):
        #logging.debug(f"Rendering template: {template_name} with context: {context}")
        template = self.env.get_template(template_name)
        return template.render(context)

    def sanitize_path(self, path):
        """Sanitize the path to remove redundant slashes and ensure no directory traversal."""
        # Decode URL-encoded characters
        path = unquote(path)
        # Normalize the path to remove redundant slashes
        normalized_path = os.path.normpath(path)
        # Prevent directory traversal by ensuring it doesn't start with '../'
        if normalized_path.startswith('..'):
            return None
        return normalized_path.lstrip('/')


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


    def get_breadcrumbs(self, main_entry_id):
        """Fetch breadcrumbs for the current entry and return a list of dictionaries."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT id, filename, level
                    FROM file_entries
                    WHERE id IN (
                            WITH RECURSIVE breadcrumb AS (
                                    SELECT id, parent_id, level FROM file_entries WHERE id = ?
                                    UNION ALL
                                    SELECT f.id, f.parent_id, f.level
                                    FROM file_entries f
                                    INNER JOIN breadcrumb b ON f.id = b.parent_id
                            )
                            SELECT id FROM breadcrumb
                    )
                    ORDER BY level ASC;
            """, (main_entry_id,))
            rows = cursor.fetchall()
            conn.close()

            # Convert rows into dictionaries for attribute-based access
            breadcrumbs = [{"id": row[0], "filename": row[1], "level": row[2]} for row in rows]
            base_path = "/".join([crumb["filename"] for crumb in breadcrumbs if crumb["id"] != 1])
            ## base_path = "/".join([crumb["filename"] for crumb in breadcrumbs])
            logging.debug(f"Fetched breadcrumbs for entry {main_entry_id}: {breadcrumbs}")
            return breadcrumbs, base_path
        except Exception as e:
            logging.error(f"Error fetching breadcrumbs: {e}")
            return []


    def get_site_map(self):
        """Fetch the full site map with content as the primary display name, falling back to filename."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT id, COALESCE(content, filename) AS display_name, parent_id, level
                    FROM file_entries
                    WHERE entry_type = 'folder'
                    ORDER BY level ASC, parent_id ASC, id ASC;
            """)
            rows = cursor.fetchall()
            conn.close()

            # Convert rows to a lookup table for fast access
            entry_lookup = {row[0]: {"id": row[0], "display_name": row[1], "parent_id": row[2], "level": row[3], "children": []} for row in rows}

            # Recursive function to build the tree
            def build_tree(parent_id):
                return [{**entry, "children": build_tree(entry["id"])} for entry in entry_lookup.values() if entry["parent_id"] == parent_id]
                #return [entry | {"children": build_tree(entry["id"])} for entry in entry_lookup.values() if entry["parent_id"] == parent_id]

            # Build the tree starting from the root (parent_id = NULL)
            site_map = build_tree(None)
            logging.debug(f"Constructed site map tree: {site_map}")

            return site_map
        except Exception as e:
            logging.error(f"Error fetching site map: {e}")
            return []

    def get_sibling_navigation(self, current_id):
        """Fetch the previous and next sibling folders for the current entry."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Fetch all sibling folders
            cursor.execute("""
                    SELECT id, COALESCE(content, filename) AS display_name
                    FROM file_entries
                    WHERE parent_id = (
                            SELECT parent_id
                            FROM file_entries
                            WHERE id = ?
                    ) AND entry_type = 'folder'
                    ORDER BY position_marker ASC, id ASC;
            """, (current_id,))

            siblings = cursor.fetchall()
            siblings = [(int(s[0]), s[1].strip()) for s in siblings]  # Normalize IDs to integers
            conn.close()
            logging.debug(f"Siblings fetched: {siblings}")

            # Find the current entry in the siblings list
            previous_entry = None
            next_entry = None
            for i, sibling in enumerate(siblings):
                logging.debug(f"Checking sibling: {sibling}")
                if sibling[0] == int(current_id):
                    if i > 0:
                        previous_entry = siblings[i - 1]
                        logging.debug(f"Set previous_entry: {previous_entry}")
                    if i < len(siblings) - 1:
                        next_entry = siblings[i + 1]
                        logging.debug(f"Set next_entry: {next_entry}")
                    break

            return previous_entry, next_entry
        except Exception as e:
            logging.error(f"Error fetching sibling navigation: {e}")
            return None, None

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
                content = self.convert_markdown(content) if content else None
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

            #logging.debug(f"Folder entry details: {folder_entry}, parsed entries: {parsed_entries}")
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
                parent_id = entry[1] if entry[1] is not None else 1
                logging.debug(f"Fetched entry: {entry}")
                return {
                        "id": entry[0],
                        "parent_id": parent_id,
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
        #path = self.sanitize_path(raw_path)
        logging.debug(f"Handling request path: {path}")
        logging.debug(f"WSGI PATH_INFO: {environ.get('PATH_INFO')}")

        try:
            if path == '/':  # Index route
                main_entries = self.get_main_entries()
                html = self.render_template('index.html', {'main_entries': main_entries})
                start_response('200 OK', [('Content-Type', 'text/html')])
                return [html.encode('utf-8')]

            elif path.startswith('/entry/'):  # Main entry details route
                parsed_url = urlparse(path)
                main_entry_id = path.split('/')[2]
                main_entry, parsed_entries = self.get_main_entry_details(main_entry_id)

                # Access query parameters from WSGI environ
                query_string = environ.get('QUERY_STRING', '')  # Get raw query string
                query_params = parse_qs(query_string)  # Parse into a dictionary

                #EDIT_MODE = query_params.get('edit', ['false'])[0].lower() == 'true'
                logging.error(f"Edit mode: {EDIT_MODE}")

                main_entry, parsed_entries = self.get_main_entry_details(main_entry_id)
                breadcrumbs , base_path = self.get_breadcrumbs(main_entry_id)
                previous_entry, next_entry = self.get_sibling_navigation(main_entry_id)
                site_map = self.get_site_map()
                if main_entry:
                    html = self.render_template('entry.html', {
                            'main_entry': main_entry,
                            'parsed_entries': parsed_entries,
                            'breadcrumbs': breadcrumbs,
                            'base_path': base_path,
                            'site_map': site_map,
                            'previous_entry': previous_entry,
                            'next_entry': next_entry,
                            'EDIT_MODE': EDIT_MODE
                    })

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

                        if parent_id is None:
                            parent_id = '1'  # Default to string '1' since query strings are strings


                        if entry_id and content is not None:
                            # Use the existing update_entry function
                            success = self.update_entry(entry_id, content)
                            if success:
                                # Redirect based on parent_id
                                if parent_id:
                                    start_response('302 Found', [('Location', f'/app/entry/{parent_id}')])
                                return [b"Redirecting..."]

                        start_response('400 Bad Request', [('Content-Type', 'text/plain')])
                        return [b"Invalid data provided"]
                    except Exception as e:
                        logging.error(f"Error in save route: {e}")
                        start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
                        return [b"Internal Server Error"]


            else:  # 404 for unknown paths
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                logging.error(f"Error serving page: {path}")
                return [b"Page not found"]

        except Exception as e:
            logging.error(f"Unhandled exception: {e}",  exc_info=True)
            start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
            return [b"Internal Server Error"]

