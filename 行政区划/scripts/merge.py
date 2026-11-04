#!/usr/bin/env python3
import sys
import csv
from operator import itemgetter

FIELDS = ['level', 'parent', 'code', 'type', 'name']

rows = []
for arg in sys.argv[1:]:
    filename, level = arg.split('@') if '@' in arg else (arg, None)
    with open(filename, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if level is not None:
                row['level'] = level
            rows.append([row[field] for field in FIELDS])

rows.sort(key=itemgetter(FIELDS.index('code')))

writer = csv.writer(sys.stdout, lineterminator='\n')
writer.writerow(FIELDS)
writer.writerows(rows)
