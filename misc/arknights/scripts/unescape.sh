  cat characters.csv \
| sed 's/&quot;/""/g' \
| perl -MHTML::Entities -pe 'decode_entities($_);' \
| sponge characters.csv
