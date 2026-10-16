#!/bin/bash
# Publish the Desktop dashboard to the always-on server location.
SRC="/Users/devanshu/Desktop/Sept 23 Companies/dashboard"
DST="$HOME/Library/Application Support/outreach-dashboard"
cp "$SRC"/index.html "$SRC"/app.js "$SRC"/data.js "$SRC"/styles.css "$DST"/ && echo "published -> http://localhost:3001"
