#!/bin/bash
set -e

INTERVAL=${ENRICHMENT_INTERVAL_HOURS:-4}

echo "0 */${INTERVAL} * * * root cd /analyzer/hestia && ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} /usr/local/bin/python3 -m enrichment.analyzer > /proc/1/fd/1 2>/proc/1/fd/2" >> /etc/crontab

cron -f
