import sys
import os

# Add the app directory to the Python path
sys.path.insert(0, '/var/www/natur-lehrpfad.de/app')

# Import the app
from nlpapp import NLPApp

# Create the application instance
#application = NLPApp(template_dir='templates', db_path='users.db')
application = NLPApp()

