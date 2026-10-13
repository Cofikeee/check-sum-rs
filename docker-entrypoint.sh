#!/bin/sh
set -eu
mkdir -p /app/data/files /app/data/imports /app/data/pages /app/data/models
exec receipt-bot
