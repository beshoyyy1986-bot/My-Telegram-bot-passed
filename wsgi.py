#!/usr/bin/env python3
import os
import sys

# Add repo to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import app

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
