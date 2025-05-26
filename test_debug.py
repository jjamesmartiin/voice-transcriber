#!/usr/bin/env python3
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Import and run the global shortcut daemon
exec(open('global_shortcut.py').read()) 